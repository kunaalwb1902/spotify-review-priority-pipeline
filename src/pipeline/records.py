"""Materialize exactly one final record per source review_id from saved state (code only, no model).

completed   - the text has a validated result under this run's config. The review that was actually
              sent is the original; byte-identical duplicates carry cache_source_id -> that original.
quarantined - empty text (empty_review_text), model output invalid after retry/fallback, or not yet
              processed (an incomplete run is never reported as completed).
"""

import json


def iter_records(con, run_config):
    has_results = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='results'").fetchone()[0]
    if not has_results:
        rows = con.execute("SELECT review_id, source_sha256, status, reason FROM reviews ORDER BY row_idx")
        for r in rows:
            yield {"review_id": r["review_id"], "source_sha256": r["source_sha256"], "status": "quarantined",
                   "reason": r["reason"] or "unprocessed: enrichment not started"}
        return
    rows = con.execute("""
        SELECT r.review_id, r.source_sha256, r.status, r.reason AS empty_reason, r.review_timestamp, r.review_rating,
               x.review_id AS original_id, x.label_config, x.topic, x.subtopic, x.intent, x.severity, x.sentiment,
               x.entities, x.evidence_quote, x.needs_review, x.via,
               q.reason AS q_reason, q.attempts AS q_attempts
        FROM reviews r
        LEFT JOIN results x ON x.text_sha256 = r.text_sha256 AND x.run_config = :rc AND r.status != 'quarantined'
        LEFT JOIN text_quarantine q ON q.text_sha256 = r.text_sha256 AND q.label_config = :rc AND r.status != 'quarantined'
        ORDER BY r.row_idx""", {"rc": run_config})
    for r in rows:
        base = {"review_id": r["review_id"], "source_sha256": r["source_sha256"]}
        if r["status"] == "quarantined":
            yield {**base, "status": "quarantined", "reason": r["empty_reason"]}
        elif r["original_id"] is not None:
            rec = {**base, "status": "completed", "topic": r["topic"], "intent": r["intent"],
                   "sentiment": r["sentiment"], "severity": r["severity"],
                   "entities": json.loads(r["entities"]), "evidence_quote": r["evidence_quote"],
                   "needs_review": bool(r["needs_review"]), "label_config": r["label_config"],
                   "subtopic": r["subtopic"]}
            if r["original_id"] != r["review_id"]:
                rec["cache_source_id"] = r["original_id"]
            yield rec
        elif r["q_reason"] is not None:
            yield {**base, "status": "quarantined", "reason": f"model_output_invalid:{r['q_reason']}",
                   "attempts": r["q_attempts"]}
        else:
            yield {**base, "status": "quarantined", "reason": "unprocessed: run incomplete (still pending)"}


def write_records(con, run_config, path):
    counts = {"completed": 0, "completed_via_cache": 0, "quarantined_empty": 0, "quarantined_model": 0,
              "unprocessed": 0}
    with open(path, "w", encoding="utf-8") as f:
        for rec in iter_records(con, run_config):
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            if rec["status"] == "completed":
                counts["completed"] += 1
                counts["completed_via_cache"] += "cache_source_id" in rec
            elif rec["reason"] == "empty_review_text":
                counts["quarantined_empty"] += 1
            elif rec["reason"].startswith("model_output_invalid"):
                counts["quarantined_model"] += 1
            else:
                counts["unprocessed"] += 1
    return counts
