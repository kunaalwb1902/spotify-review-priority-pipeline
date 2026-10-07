"""Stage 5 - Rank. Code only; NEVER calls a model. Regenerates identically from saved files.

Input:  records.jsonl + membership.csv (+ issues.csv for names only).
Rule:   complaint_count = members; severity_sum = sum(member severity);
        mean_severity = severity_sum / complaint_count (Decimal, 6 dp, half-up - checker's own helper);
        priority_score = severity_sum (= count x mean, with no rounding loss).
Order:  priority_score descending, then issue_id ascending. Ranks start at 1.
Output: ranking.csv (contract columns), aggregates.csv (+ names, cancellations, sev>=4, needs_review),
        area_rollup.csv (the four product areas in the business question), monthly_trend.csv.
"""

import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

from .common import checker
from .group import PRODUCT_AREA, read_records

mean_string = checker.mean_string            # identical rounding/format to the grader


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def compute(records_path, membership_path):
    recs = {r["review_id"]: r for r in read_records(records_path) if r["status"] == "completed"}
    by_issue = defaultdict(list)
    seen = set()
    for row in _read_csv(membership_path):
        iid, rid = row["issue_id"], row["review_id"]
        assert (iid, rid) not in seen, f"duplicate membership {iid},{rid}"
        seen.add((iid, rid))
        rec = recs[rid]
        assert rec["intent"] in ("complaint", "cancellation"), f"non-complaint in membership: {rid}"
        by_issue[iid].append(rec)
    complaints = {rid for rid, r in recs.items() if r["intent"] in ("complaint", "cancellation")}
    grouped = {rid for _, rid in seen}
    assert complaints == grouped, f"{len(complaints - grouped)} complaints not in any issue"

    rows = []
    for iid, members in by_issue.items():
        s = sum(m["severity"] for m in members)
        rows.append({"issue_id": iid, "complaint_count": len(members), "severity_sum": s,
                     "mean_severity": mean_string(s, len(members)), "priority_score": s,
                     "cancellation_count": sum(m["intent"] == "cancellation" for m in members),
                     "severity_4_plus": sum(m["severity"] >= 4 for m in members),
                     "severity_5": sum(m["severity"] == 5 for m in members),
                     "needs_review_count": sum(m["needs_review"] for m in members)})
    rows.sort(key=lambda r: (-r["priority_score"], r["issue_id"]))
    for rank, r in enumerate(rows, 1):
        r["rank"] = rank
    return rows, recs


def area_rollup(rows, recs):
    total_completed = len(recs)
    total_complaints = sum(r["complaint_count"] for r in rows)
    acc = defaultdict(lambda: Counter())
    for r in rows:
        topic = r["issue_id"].split("-", 2)[1]
        a = acc[PRODUCT_AREA[topic]]
        for k in ("complaint_count", "severity_sum", "cancellation_count", "severity_4_plus", "severity_5"):
            a[k] += r[k]
        a["issues"] += 1
    out = []
    for area, a in acc.items():
        out.append({"product_area": area, "issues": a["issues"], "complaint_count": a["complaint_count"],
                    "severity_sum": a["severity_sum"], "mean_severity": mean_string(a["severity_sum"], a["complaint_count"]),
                    "share_of_complaints": mean_string(a["complaint_count"], total_complaints),
                    "cancellation_count": a["cancellation_count"], "severity_4_plus": a["severity_4_plus"],
                    "severity_5": a["severity_5"], "completed_records_denominator": total_completed,
                    "complaints_denominator": total_complaints})
    out.sort(key=lambda r: (-r["severity_sum"], r["product_area"]))
    return out


def monthly_trend(recs, timestamps):
    """Complaint share per month by product area, with denominators (first/last months are partial)."""
    totals, comp = Counter(), defaultdict(Counter)
    for rid, r in recs.items():
        month = timestamps.get(rid, "")[:7]
        totals[month] += 1
        if r["intent"] in ("complaint", "cancellation"):
            comp[month][PRODUCT_AREA[r["topic"]]] += 1
    rows = []
    for month in sorted(totals):
        for area in sorted(set(PRODUCT_AREA.values())):
            rows.append({"month": month, "product_area": area, "complaints": comp[month][area],
                         "completed_records": totals[month],
                         "share": mean_string(comp[month][area], totals[month])})
    return rows


def write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: str(v) for k, v in r.items() if k in cols})


def rank(out_dir, timestamps=None):
    out = Path(out_dir)
    rows, recs = compute(out / "records.jsonl", out / "membership.csv")
    names = {r["issue_id"]: r for r in _read_csv(out / "issues.csv")} if (out / "issues.csv").exists() else {}
    for r in rows:
        r["name"] = names.get(r["issue_id"], {}).get("name", "")
        r["product_area"] = PRODUCT_AREA[r["issue_id"].split("-", 2)[1]]
    write_csv(out / "ranking.csv", rows,
              ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"])
    write_csv(out / "aggregates.csv", rows,
              ["rank", "issue_id", "name", "product_area", "complaint_count", "severity_sum", "mean_severity",
               "priority_score", "cancellation_count", "severity_4_plus", "severity_5", "needs_review_count"])
    areas = area_rollup(rows, recs)
    write_csv(out / "area_rollup.csv", areas, list(areas[0]) if areas else ["product_area"])
    if timestamps:
        trend = monthly_trend(recs, timestamps)
        write_csv(out / "monthly_trend.csv", trend, ["month", "product_area", "complaints", "completed_records", "share"])
    digest = hashlib.sha256((out / "ranking.csv").read_bytes()).hexdigest()
    return rows, areas, digest
