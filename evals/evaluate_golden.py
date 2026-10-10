"""Compare model enrichment against the human-labeled golden 50. Code only; no model calls.

    python evals/evaluate_golden.py --run runs/golden50

Predeclared rules (set before seeing results):
  - topic / intent: exact match = strict agreement; "lenient" also accepts a value the human listed in
    acceptable_alternatives (e.g. "topic=other; intent=unclear").
  - severity: exact agreement, mean absolute error, and within-1.
  - sentiment: mean absolute error; "agree" if |model - human| <= 0.3.
  - evidence_quote: exact substring checked in code; whether it SUPPORTS the label is a human judgment
    (cases listed in the report for inspection).
  - entities: an entity is "unsupported" if its text does not occur (case-insensitive) in the review.
  - needs_review: evaluated as a prediction of the human flagging the case (needs_review OR ambiguous).
  - Missing/quarantined model predictions count as incorrect (kept in every denominator).
Human labels: evals/golden_50_human.csv, frozen in git before any model saw these reviews.
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOPICS = ["access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"]
INTENTS = ["cancellation", "complaint", "request", "praise", "unclear"]
SENT_TOL = 0.3


def alts(s):
    out = defaultdict(set)
    for part in re.split(r"[;,]", s or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()].add(v.strip().lower())
    return out


def f1_per_class(pairs, classes):
    res = {}
    for c in classes:
        tp = sum(h == c and m == c for h, m in pairs)
        fp = sum(h != c and m == c for h, m in pairs)
        fn = sum(h == c and m != c for h, m in pairs)
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        f = 2 * p * r / (p + r) if p and r else (0.0 if (tp + fn) else None)
        res[c] = {"support": tp + fn, "predicted": tp + fp, "precision": p, "recall": r, "f1": f}
    present = [v["f1"] for v in res.values() if v["support"]]
    return res, (sum(present) / len(present) if present else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="runs/golden50")
    ap.add_argument("--out", default="evals/golden")
    a = ap.parse_args()
    run, out = ROOT / a.run, ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    human = list(csv.DictReader(open(ROOT / "evals" / "golden_50_human.csv", newline="", encoding="utf-8")))
    model = {}
    with open(run / "records.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            model[r["review_id"]] = r

    rows = []
    for h in human:
        m = model.get(h["review_id"], {})
        ok = m.get("status") == "completed"
        al = alts(h["acceptable_alternatives"])
        text = h["review_text"]
        hs, hsent = int(h["severity"]), float(h["sentiment"])
        row = {"review_id": h["review_id"], "text": text, "model_status": m.get("status", "missing"),
               "human_topic": h["topic"], "model_topic": m.get("topic"),
               "human_intent": h["intent"], "model_intent": m.get("intent"),
               "human_severity": hs, "model_severity": m.get("severity"),
               "human_sentiment": hsent, "model_sentiment": m.get("sentiment"),
               "human_quote": h["evidence_quote"], "model_quote": m.get("evidence_quote"),
               "model_entities": "|".join(m.get("entities", [])),
               "human_flag": h["needs_review"] == "true" or h["ambiguous"] == "true",
               "model_needs_review": m.get("needs_review"), "human_ambiguous": h["ambiguous"] == "true",
               "acceptable_alternatives": h["acceptable_alternatives"]}
        row["topic_strict"] = ok and m["topic"] == h["topic"]
        row["topic_lenient"] = row["topic_strict"] or (ok and m["topic"] in al.get("topic", set()))
        row["intent_strict"] = ok and m["intent"] == h["intent"]
        row["intent_lenient"] = row["intent_strict"] or (ok and m["intent"] in al.get("intent", set()))
        row["severity_exact"] = ok and m["severity"] == hs
        row["severity_abs_err"] = abs(m["severity"] - hs) if ok else None
        row["sentiment_abs_err"] = round(abs(m["sentiment"] - hsent), 2) if ok else None
        row["sentiment_agree"] = ok and abs(m["sentiment"] - hsent) <= SENT_TOL + 1e-9
        row["quote_is_substring"] = ok and m["evidence_quote"] in text
        row["quote_overlaps_human"] = ok and (m["evidence_quote"] in h["evidence_quote"] or h["evidence_quote"] in m["evidence_quote"])
        row["unsupported_entities"] = "|".join(e for e in m.get("entities", []) if e.lower() not in text.lower())
        row["all_core_fields_agree"] = row["topic_strict"] and row["intent_strict"] and row["severity_exact"]
        rows.append(row)

    n = len(rows)
    ok_rows = [r for r in rows if r["model_status"] == "completed"]
    pct = lambda k: round(sum(bool(r[k]) for r in rows) / n, 4)
    tp_pairs = [(r["human_topic"], r["model_topic"]) for r in rows]
    in_pairs = [(r["human_intent"], r["model_intent"]) for r in rows]
    topic_f1, topic_macro = f1_per_class(tp_pairs, TOPICS)
    intent_f1, intent_macro = f1_per_class(in_pairs, INTENTS)
    flag_tp = sum(r["human_flag"] and r["model_needs_review"] is True for r in rows)
    flag_pred = sum(r["model_needs_review"] is True for r in rows)
    flag_true = sum(r["human_flag"] for r in rows)
    summary = {
        "n": n, "model_completed": len(ok_rows), "missing_or_quarantined_counted_wrong": n - len(ok_rows),
        "human_ambiguous_cases": sum(r["human_ambiguous"] for r in rows),
        "topic_agreement_strict": pct("topic_strict"), "topic_agreement_lenient": pct("topic_lenient"),
        "topic_macro_f1": round(topic_macro, 4) if topic_macro is not None else None,
        "intent_agreement_strict": pct("intent_strict"), "intent_agreement_lenient": pct("intent_lenient"),
        "intent_macro_f1": round(intent_macro, 4) if intent_macro is not None else None,
        "severity_exact_agreement": pct("severity_exact"),
        "severity_mae": round(sum(r["severity_abs_err"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else None,
        "severity_within_1": round(sum(r["severity_abs_err"] is not None and r["severity_abs_err"] <= 1 for r in rows) / n, 4),
        "sentiment_mae": round(sum(r["sentiment_abs_err"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else None,
        "sentiment_within_tolerance": pct("sentiment_agree"), "sentiment_tolerance": SENT_TOL,
        "quote_exact_substring": pct("quote_is_substring"), "quote_overlaps_human_quote": pct("quote_overlaps_human"),
        "records_with_unsupported_entities": sum(bool(r["unsupported_entities"]) for r in rows),
        "needs_review_as_prediction": {"model_flagged": flag_pred, "human_flagged": flag_true, "both": flag_tp,
                                       "precision": round(flag_tp / flag_pred, 4) if flag_pred else None,
                                       "recall": round(flag_tp / flag_true, 4) if flag_true else None},
        "all_three_core_fields_agree": pct("all_core_fields_agree"),
        "per_topic": topic_f1, "per_intent": intent_f1,
        "note": "50 cases is a small diagnostic sample, not a population accuracy estimate.",
    }
    cols = list(rows[0])
    with open(out / "golden_comparison.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    with open(out / "golden_disagreements.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(r for r in rows if not r["all_core_fields_agree"])
    for name, pairs, classes in (("topic", tp_pairs, TOPICS), ("intent", in_pairs, INTENTS)):
        conf = Counter(pairs)
        with open(out / f"confusion_{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"human \\ model"] + classes + ["(missing)"])
            for h in classes:
                w.writerow([h] + [conf[(h, m)] for m in classes] + [conf[(h, None)]])
    (out / "golden_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("per_topic", "per_intent")}, indent=2))
    print("\nconfusion (topic):")
    print(open(out / "confusion_topic.csv").read())


if __name__ == "__main__":
    main()
