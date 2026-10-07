"""Stage 1 - Prepare. Code only, no model.

Input:  any CSV with the six source columns.
Output: SQLite `reviews` table (raw values + source_sha256 + text_sha256 + status)
        and ingestion_report.json (profile, duplicates, quarantines, manifest comparison).
Stop:   aborts on a malformed CSV or duplicate review_id rather than guessing.
"""

import json
import time
from collections import Counter

from .common import FIELDS, RAW, connect, csv_rows, file_sha256, is_empty, row_sha, text_sha256, write_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id        TEXT PRIMARY KEY,
    row_idx          INTEGER NOT NULL,
    review_text      TEXT NOT NULL,
    review_rating    TEXT NOT NULL,
    review_likes     TEXT NOT NULL,
    app_version      TEXT NOT NULL,
    review_timestamp TEXT NOT NULL,
    source_sha256    TEXT NOT NULL,
    text_sha256      TEXT NOT NULL,
    status           TEXT NOT NULL,      -- pending | completed | quarantined
    reason           TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_text ON reviews(text_sha256);
CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);
"""


def ingest(csv_path, db_path, report_path):
    started = time.time()
    con = connect(db_path)
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]:
        raise SystemExit(f"{db_path} already has reviews; use a fresh --db for a new input file.")

    counts = Counter()
    ratings, months, text_counts = Counter(), Counter(), Counter()
    seen_ids = set()
    first = last = None
    batch = []

    for idx, row in enumerate(csv_rows(csv_path)):
        rid = row["review_id"]
        if rid in seen_ids:
            raise SystemExit(f"Duplicate review_id {rid}; refusing to continue.")
        seen_ids.add(rid)
        text = row["review_text"]
        empty = is_empty(text)
        counts["records"] += 1
        counts["empty_review_text"] += empty
        counts["missing_app_version"] += not row["app_version"].strip()
        ratings[row["review_rating"]] += 1
        months[row["review_timestamp"][:7]] += 1
        ts = row["review_timestamp"]
        first = min(first, ts) if first else ts
        last = max(last, ts) if last else ts
        tsha = text_sha256(text)
        if not empty:
            text_counts[tsha] += 1
        batch.append((rid, idx, *(row[f] for f in FIELDS[1:]), row_sha(row), tsha,
                      "quarantined" if empty else "pending", "empty_review_text" if empty else None))
        if len(batch) >= 20000:
            con.executemany("INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
            batch.clear()
    con.executemany("INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
    con.commit()

    nonempty = counts["records"] - counts["empty_review_text"]
    distinct = len(text_counts)
    dup_groups = sum(1 for c in text_counts.values() if c > 1)
    report = {
        "input_path": str(csv_path),
        "input_sha256": file_sha256(csv_path),
        "counts": {
            "records": counts["records"],
            "duplicate_review_ids": 0,
            "empty_review_text_quarantined": counts["empty_review_text"],
            "nonempty_to_classify": nonempty,
            "missing_app_version": counts["missing_app_version"],
            "distinct_nonempty_texts": distinct,
            "exact_duplicate_text_groups": dup_groups,
            "rows_reusable_by_exact_text": nonempty - distinct,
        },
        "first_review": first,
        "last_review": last,
        "reviews_by_rating": dict(sorted(ratings.items())),
        "reviews_by_month": dict(sorted(months.items())),
        "most_repeated_texts": [
            {"text": t, "count": c} for t, c in _top_texts(con, text_counts, 15)
        ],
        "quarantine_rule": "review_text.strip() == '' -> status quarantined, reason empty_review_text",
        "notes": "Missing app_version does not block classification. Raw values are stored unchanged.",
        "seconds": round(time.time() - started, 2),
    }
    report["manifest_check"] = _compare_manifest(report)
    write_json(report_path, report)
    return report


def _top_texts(con, text_counts, n):
    out = []
    for tsha, c in text_counts.most_common(n):
        text = con.execute("SELECT review_text FROM reviews WHERE text_sha256=? LIMIT 1", (tsha,)).fetchone()[0]
        out.append((text, c))
    return out


def _compare_manifest(report):
    """Only meaningful for the full course file; reports match/mismatch per field."""
    manifest_path = RAW / "manifest.json"
    if not manifest_path.exists():
        return {"status": "no_manifest"}
    m = json.loads(manifest_path.read_text())
    full = m["files"]["spotify_reviews_18months.csv"]["sha256"]
    if report["input_sha256"] != full:
        return {"status": "not_the_full_file"}
    c, p = report["counts"], m["profile"]
    checks = {
        "records": (c["records"], p["records"]),
        "empty_review_text": (c["empty_review_text_quarantined"], p["empty_review_text"]),
        "missing_app_version": (c["missing_app_version"], p["missing_app_version"]),
        "duplicate_review_ids": (c["duplicate_review_ids"], p["duplicate_review_ids"]),
        "distinct_nonempty_texts": (c["distinct_nonempty_texts"], 484189),  # stated in assignment brief
        "reviews_by_month": (report["reviews_by_month"], m["reviews_by_month"]),
        "reviews_by_rating": (report["reviews_by_rating"], m["reviews_by_rating"]),
        "first_review": (report["first_review"], m["window"]["first_review"]),
        "last_review": (report["last_review"], m["window"]["last_review"]),
    }
    detail = {k: {"ours": a, "expected": b, "match": a == b} for k, (a, b) in checks.items()
              if not isinstance(a, dict)}
    detail.update({k: {"match": a == b} for k, (a, b) in checks.items() if isinstance(a, dict)})
    return {"status": "all_match" if all(v["match"] for v in detail.values()) else "MISMATCH", "fields": detail}
