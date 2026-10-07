"""Stage 6 - Recommend. The memo model sees ONLY a bounded evidence pack built by code from saved
aggregates; it writes placeholders instead of numbers, and code renders + checks every claim.

Outputs: evidence_pack.json, memo_raw.md (model text), memo.md (rendered), claims.csv
         (issue-level claims in the contract format), area_claims.csv, memo_check.json.
The run fails the check if the memo cites unknown issues/review IDs/placeholders or types raw numbers.
"""

import csv
import json
import re
from pathlib import Path

from .common import ROOT
from .runtime import cache_get, cache_key, cache_put

TOP_ISSUES = 12
QUOTES_PER_ISSUE = 3
ISSUE_METRICS = ("complaint_count", "severity_sum", "mean_severity", "priority_score", "cancellation_count",
                 "severity_4_plus")
CONTRACT_METRICS = {"complaint_count", "severity_sum", "mean_severity", "priority_score"}
AREA_METRICS = ("complaint_count", "severity_sum", "mean_severity", "share_of_complaints", "cancellation_count",
                "severity_4_plus")
PLACEHOLDER = re.compile(r"\{(AREA:[a-z_]+|RUN|ISS-[a-z_]+-[a-z_]+)\.([a-z_0-9]+)\}")
REVIEW_REF = re.compile(r"\[review ([^\]\s]+)\]")        # any source ID format


def _csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_pack(run_dir, run_summary):
    run_dir = Path(run_dir)
    aggs = _csv(run_dir / "aggregates.csv")[:TOP_ISSUES]
    issues = {r["issue_id"]: r for r in _csv(run_dir / "issues.csv")}
    quotes = {}
    with open(run_dir / "records.jsonl", encoding="utf-8") as f:
        wanted = {rid for a in aggs for rid in issues[a["issue_id"]]["example_review_ids"].split("|")[:QUOTES_PER_ISSUE] if rid}
        for line in f:
            r = json.loads(line)
            if r["review_id"] in wanted:
                quotes[r["review_id"]] = r["evidence_quote"]
    top = []
    for a in aggs:
        meta = issues[a["issue_id"]]
        ex = [rid for rid in meta["example_review_ids"].split("|")[:QUOTES_PER_ISSUE] if rid in quotes]
        top.append({"issue_id": a["issue_id"], "rank": int(a["rank"]), "name": meta["name"],
                    "description": meta["description"], "product_area": a["product_area"],
                    **{m: a[m] for m in ISSUE_METRICS},
                    "examples": [{"review_id": rid, "quote": quotes[rid]} for rid in ex]})
    areas = _csv(run_dir / "area_rollup.csv")
    return {"question": "Where should next quarter's product effort go: access, usability, playback, or billing/support?",
            "ranking_rule": "priority_score = severity_sum = complaint_count x mean_severity over complaint+cancellation records; ties by issue_id",
            "run": run_summary, "product_areas": areas, "top_issues": top}


def lookup_tables(pack):
    issue = {(t["issue_id"], m): t[m] for t in pack["top_issues"] for m in ISSUE_METRICS}
    area = {(f"AREA:{a['product_area']}", m): a[m] for a in pack["product_areas"] for m in AREA_METRICS}
    run = {("RUN", k): v for k, v in pack["run"].items()}
    return {**issue, **area, **run}


def render_and_check(raw, pack):
    table = lookup_tables(pack)
    known_issues = {t["issue_id"] for t in pack["top_issues"]}
    known_reviews = {e["review_id"]: e["quote"] for t in pack["top_issues"] for e in t["examples"]}
    problems = []

    # 1. Assign claim IDs in first-use order: contract metrics -> C01.. (claims.csv); others -> X01..
    ids, claims, other = {}, [], []
    for m in PLACEHOLDER.finditer(raw):
        key = (m.group(1), m.group(2))
        if key in ids:
            continue
        if key not in table:
            problems.append(f"unknown placeholder {{{key[0]}.{key[1]}}}")
            continue
        if key[0].startswith("ISS-") and key[1] in CONTRACT_METRICS:
            ids[key] = f"C{len(claims) + 1:02d}"
            claims.append({"claim_id": ids[key], "issue_id": key[0], "metric": key[1], "value": str(table[key])})
        elif key[0] == "RUN":
            ids[key] = None                                 # run accounting: from run_summary, not a claim row
        else:
            ids[key] = f"X{len(other) + 1:02d}"
            other.append({"claim_id": ids[key], "subject": key[0], "metric": key[1], "value": str(table[key])})

    # 2. Raw digits typed by the model are not allowed (placeholders, IDs and list markers excluded).
    scrubbed = PLACEHOLDER.sub(" ", raw)
    scrubbed = REVIEW_REF.sub(" ", scrubbed)
    scrubbed = re.sub(r"ISS-[a-z_]+-[a-z_]+", " ", scrubbed)
    scrubbed = re.sub(r"(?m)^\s*(#+\s*)?\d+[.)]\s", " ", scrubbed)
    for d in re.findall(r"(?<![A-Za-z_])\d[\d,.]*%?", scrubbed):
        problems.append(f"raw number typed by model: {d!r}")

    # 3. Only IDs from the evidence pack.
    for iid in sorted(set(re.findall(r"ISS-[a-z_]+-[a-z_]+", raw))):
        if iid not in known_issues:
            problems.append(f"issue id not in evidence pack: {iid}")
    for rid in REVIEW_REF.findall(raw):
        if rid not in known_reviews:
            problems.append(f"review id not in evidence pack: {rid}")

    # 4. Render: every number comes from the saved table and carries its claim ID.
    def sub(m):
        key = (m.group(1), m.group(2))
        if key not in table:
            return m.group(0)
        tag = ids.get(key)
        return f"{table[key]} [{tag}]" if tag else f"{table[key]}"
    return PLACEHOLDER.sub(sub, raw), claims, other, problems


def write_memo(run_dir, con, llm, run_cfg, run_summary):
    run_dir = Path(run_dir)
    pack = build_pack(run_dir, run_summary)
    (run_dir / "evidence_pack.json").write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n")
    system = (ROOT / "prompts" / "memo_v1.md").read_text(encoding="utf-8")
    m = run_cfg["memo"]
    key = cache_key("memo", system, pack, m["model"])
    raw, req = cache_get(con, key)
    new_calls = 0
    if raw is None:
        user = "<evidence>\n" + json.dumps(pack, ensure_ascii=False) + "\n</evidence>"
        res = llm.call(role="memo", model=m["model"], system=system, user=user, max_tokens=m.get("max_tokens", 8000),
                       review_ids=[], temperature=None, purpose="write_memo", cache_system=False,
                       effort=m.get("effort", "low"))
        new_calls = 1
        if not res.ok:
            raise RuntimeError(f"memo call failed: {res.error}")
        raw, req = res.text, res.request_ids[-1]
        cache_put(con, key, "memo", raw, req)
    rendered, claims, area_claims, problems = render_and_check(raw, pack)
    (run_dir / "memo_raw.md").write_text(raw)
    trace = "\n\n---\n_Claim trace (code-generated):_ " + "; ".join(
        f"{c['claim_id']} = `{c['issue_id']}` {c['metric']} {c['value']}" for c in claims) + "\n"
    (run_dir / "memo.md").write_text(rendered + trace)
    for path, rows, cols in ((run_dir / "claims.csv", claims, ["claim_id", "issue_id", "metric", "value"]),
                             (run_dir / "area_claims.csv", area_claims, ["claim_id", "subject", "metric", "value"])):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    check = {"passed": not problems, "problems": problems, "contract_claims": len(claims),
             "other_claims": len(area_claims), "new_memo_calls": new_calls, "memo_request_id": req}
    (run_dir / "memo_check.json").write_text(json.dumps(check, indent=2) + "\n")
    return check
