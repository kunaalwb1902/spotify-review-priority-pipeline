"""Stage 3 - Verify. A separate model role re-labels a declared random sample WITHOUT seeing the
enricher's prediction; code compares field by field and writes a disagreement report.

Sample:  completed ORIGINAL records (no cache aliases), ordered by sha256(seed:review_id), first
         max(min_n, ceil(fraction x originals)). Deterministic and declared in run.yaml.
Planted-error test: a SYNTHETIC copy of sampled enricher labels with one field deliberately corrupted
         per case; the same blind verifier labels are compared against it. Detection = verifier disagrees.
         Synthetic cases are written to a separate file and never enter business aggregates.
Outputs: evals/<run>/verifier_predictions.csv, verifier_disagreements.csv, planted_errors.csv,
         verifier_summary.json
"""

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from .common import ROOT
from .group import read_records
from .runtime import cache_get, cache_key, cache_put

BATCH = 25


def render_verifier_system(labels):
    tpl = (ROOT / "prompts" / "verifier_v1.md").read_text(encoding="utf-8")
    topics = "\n".join(f"- `{t}`: {v['definition'].strip()}" for t, v in labels["topics"].items())
    intents = "\n".join(f"{n}. `{k}`: {v}" for n, (k, v) in enumerate(labels["intents"].items(), 1))
    severity = "\n".join(f"- {k}: {v}" for k, v in labels["severity"].items())
    rules = "\n".join(f"- {r}" for r in labels["rules"])
    return (tpl.replace("{topics}", topics).replace("{intents}", intents)
               .replace("{severity}", severity).replace("{rules}", rules))


def verifier_schema(labels):
    item = {"type": "object", "additionalProperties": False, "required": ["i", "topic", "intent", "sev", "unsure"],
            "properties": {"i": {"type": "integer"}, "topic": {"type": "string", "enum": list(labels["topics"])},
                           "intent": {"type": "string", "enum": list(labels["intents"])},
                           "sev": {"type": "integer", "enum": [1, 2, 3, 4, 5]}, "unsure": {"type": "boolean"}}}
    return {"type": "object", "additionalProperties": False, "required": ["results"],
            "properties": {"results": {"type": "array", "items": item}}}


def choose_sample(records_path, seed, fraction, min_n):
    originals = [r for r in read_records(records_path) if r["status"] == "completed" and "cache_source_id" not in r]
    n = min(len(originals), max(min_n, math.ceil(fraction * len(originals))))
    originals.sort(key=lambda r: hashlib.sha256(f"{seed}:{r['review_id']}".encode()).hexdigest())
    return originals[:n], len(originals)


def run_verifier(con, llm, run_cfg, labels, sample, texts, workers=4):
    """Returns {review_id: {topic, intent, sev, unsure}} and the number of new model calls.
    Chunks run concurrently; every DB touch (cache + call log) goes through llm.db_lock."""
    from concurrent.futures import ThreadPoolExecutor
    v = run_cfg["verify"]
    system, schema = render_verifier_system(labels), verifier_schema(labels)
    chunks = [sample[k:k + BATCH] for k in range(0, len(sample), BATCH)]

    def one(chunk):
        items = [{"i": n, "text": texts[r["review_id"]]} for n, r in enumerate(chunk, 1)]
        key = cache_key("verify", system, schema, v["model"], items)
        with llm.db_lock:
            cached, _ = cache_get(con, key)
        called = 0
        if cached is None:
            user = (f"Label each of these {len(items)} reviews.\n<reviews>\n"
                    + json.dumps(items, ensure_ascii=False) + "\n</reviews>")
            res = llm.call(role="verify", model=v["model"], system=system, user=user, schema=schema,
                           max_tokens=40 * len(items) + 200, review_ids=[r["review_id"] for r in chunk],
                           temperature=0, purpose="blind_relabel", cache_system=False)
            called = 1
            if not res.ok:
                return {}, called                             # missing verifier labels are reported, not invented
            try:
                cached = json.loads(res.text)["results"]
            except (ValueError, KeyError):
                return {}, called
            with llm.db_lock:
                cache_put(con, key, "verify", cached, res.request_ids[-1])
        by_i = {x.get("i"): x for x in cached if isinstance(x, dict)}
        got = {}
        for n, r in enumerate(chunk, 1):
            x = by_i.get(n)
            if x and x.get("topic") in labels["topics"] and x.get("intent") in labels["intents"] \
                    and x.get("sev") in (1, 2, 3, 4, 5) and isinstance(x.get("unsure"), bool):
                got[r["review_id"]] = {"topic": x["topic"], "intent": x["intent"], "sev": x["sev"], "unsure": x["unsure"]}
        return got, called

    out, new_calls = {}, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for got, called in pool.map(one, chunks):
            out.update(got)
            new_calls += called
    return out, new_calls


def compare(sample, verdicts, texts):
    rows = []
    for r in sample:
        v = verdicts.get(r["review_id"])
        row = {"review_id": r["review_id"], "text": texts[r["review_id"]],
               "enrich_topic": r["topic"], "enrich_intent": r["intent"], "enrich_severity": r["severity"],
               "enrich_needs_review": r["needs_review"], "evidence_quote": r["evidence_quote"]}
        if v is None:
            rows.append({**row, "verifier_status": "missing"})
            continue
        row.update({"verifier_status": "ok", "verify_topic": v["topic"], "verify_intent": v["intent"],
                    "verify_severity": v["sev"], "verify_unsure": v["unsure"],
                    "topic_agree": v["topic"] == r["topic"], "intent_agree": v["intent"] == r["intent"],
                    "severity_abs_diff": abs(v["sev"] - r["severity"])})
        row["disagree_fields"] = "|".join(
            f for f, bad in (("topic", not row["topic_agree"]), ("intent", not row["intent_agree"]),
                             ("severity", row["severity_abs_diff"] >= 2)) if bad)
        rows.append(row)
    return rows


PLANTS = [  # (field, how to corrupt) - each makes the enricher label clearly wrong
    ("topic", lambda r: "support" if r["topic"] != "support" else "downloads"),
    ("intent", lambda r: "praise" if r["intent"] in ("complaint", "cancellation") else "cancellation"),
    ("severity", lambda r: 5 if r["severity"] <= 2 else 1),
]


def planted_errors(rows):
    """Synthetic copy: corrupt one field per verified case; check the comparison flags it."""
    out = []
    ok_rows = [r for r in rows if r["verifier_status"] == "ok"]
    for n, r in enumerate(ok_rows):
        field, corrupt = PLANTS[n % len(PLANTS)]
        src = {"topic": r["enrich_topic"], "intent": r["enrich_intent"], "severity": r["enrich_severity"]}
        planted = corrupt(src)
        if field == "topic":
            caught = r["verify_topic"] != planted
        elif field == "intent":
            caught = r["verify_intent"] != planted
        else:
            caught = abs(r["verify_severity"] - planted) >= 2
        out.append({"synthetic_case_id": f"SYN-PLANT-{n + 1:04d}", "source_review_id": r["review_id"],
                    "field": field, "original_label": src[field], "planted_wrong_label": planted,
                    "verifier_label": r[f"verify_{field}"], "caught": caught, "synthetic": True})
    return out


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(dict.fromkeys(k for r in rows for k in r)) or ["empty"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def summarize(rows, planted, population, cfg):
    ok = [r for r in rows if r["verifier_status"] == "ok"]
    n = len(ok)
    pct = lambda k: round(sum(r[k] for r in ok) / n, 4) if n else None
    dis = [r for r in ok if r["disagree_fields"]]
    return {
        "declared_sample": {"seed": cfg["seed"], "fraction": cfg["sample_fraction"], "min_n": cfg.get("min_n", 10),
                            "population_completed_originals": population, "sampled": len(rows)},
        "verifier_missing": len(rows) - n,
        "topic_agreement": pct("topic_agree"), "intent_agreement": pct("intent_agree"),
        "severity_exact_agreement": round(sum(r["severity_abs_diff"] == 0 for r in ok) / n, 4) if n else None,
        "severity_mae": round(sum(r["severity_abs_diff"] for r in ok) / n, 4) if n else None,
        "disagreements": len(dis),
        "disagreement_fields": dict(Counter(f for r in dis for f in r["disagree_fields"].split("|"))),
        "verifier_unsure": sum(r["verify_unsure"] for r in ok),
        "enricher_needs_review_among_disagreements": sum(bool(r["enrich_needs_review"]) for r in dis),
        "planted_errors": {"cases": len(planted), "caught": sum(p["caught"] for p in planted),
                           "by_field": {f: f"{sum(p['caught'] for p in planted if p['field'] == f)}/"
                                           f"{sum(p['field'] == f for p in planted)}" for f, _ in PLANTS}},
        "note": "Agreement between two model roles is a consistency signal, not accuracy; the golden set measures accuracy.",
    }


def verify(run_dir, con, llm, run_cfg, labels, eval_dir):
    run_dir, eval_dir = Path(run_dir), Path(eval_dir)
    v = run_cfg["verify"]
    sample, population = choose_sample(run_dir / "records.jsonl", v["seed"], v["sample_fraction"], v.get("min_n", 10))
    texts = {r[0]: r[1] for r in con.execute("SELECT review_id, review_text FROM reviews")}
    verdicts, new_calls = run_verifier(con, llm, run_cfg, labels, sample, texts)
    rows = compare(sample, verdicts, texts)
    planted = planted_errors(rows)
    write_csv(eval_dir / "verifier_predictions.csv", rows)
    write_csv(eval_dir / "verifier_disagreements.csv", [r for r in rows if r.get("disagree_fields")])
    write_csv(eval_dir / "planted_errors.csv", planted)
    summary = summarize(rows, planted, population, v)
    summary["new_verify_calls"] = new_calls
    (eval_dir / "verifier_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
