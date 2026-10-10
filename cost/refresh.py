"""Scaling refresh: measured enrichment economics at 100 -> 500 -> 10,000 reviews, read from saved run
databases (no API). Writes cost/scaling_refresh.md and .json.   Run:  python cost/refresh.py
"""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = [("pilot100 (cost_100.csv)", "runs/pilot100"), ("dev500 (checkpoint_500.csv)", "runs/dev500"),
        ("dev10k (analysis_10000.csv)", "runs/dev10k")]
FULL_DISTINCT = 484_189


def measure(db):
    con = sqlite3.connect(db)
    q = lambda sql: con.execute(sql).fetchone()
    texts = q("SELECT COUNT(DISTINCT text_sha256) FROM reviews WHERE status!='quarantined'")[0]
    rows = q("SELECT COUNT(*) FROM reviews")[0]
    e = q("""SELECT COUNT(*), SUM(outcome='failed'), SUM(input_tokens), SUM(cache_creation_input_tokens),
             SUM(cache_read_input_tokens), SUM(output_tokens), SUM(cost_usd), GROUP_CONCAT(DISTINCT tier),
             SUM(purpose='retry_invalid'), SUM(purpose='fallback')
             FROM calls WHERE role='enrich'""")
    s = q("SELECT MIN(started_at), MAX(ended_at) FROM sessions WHERE role='enrich' AND texts_completed > 0")
    invalid = q("SELECT COUNT(DISTINCT text_sha256) FROM validation_failures")[0] if q(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='validation_failures'")[0] else None
    quarantined = q("SELECT COUNT(*) FROM text_quarantine")[0]
    other = q("SELECT COALESCE(SUM(cost_usd),0) FROM calls WHERE role!='enrich'")[0]
    sess = q("SELECT SUM(ended_at - started_at) FROM sessions WHERE role='enrich' AND texts_completed > 0")[0]
    return {"rows": rows, "distinct_texts": texts, "enrich_requests": e[0], "enrich_failed_calls": e[1],
            "retry_requests": e[8], "fallback_requests": e[9], "tier": e[7],
            "tokens": {"input": e[2], "cache_write": e[3], "cache_read": e[4], "output": e[5]},
            "output_tokens_per_text": round(e[5] / texts, 1), "enrich_cost_usd": round(e[6], 6),
            "enrich_cost_per_text_usd": e[6] / texts, "texts_with_any_invalid_output": invalid,
            "invalid_rate": round(invalid / texts, 4) if invalid is not None else None,
            "quarantined_texts": quarantined, "quarantine_rate": round(quarantined / texts, 4),
            "downstream_cost_usd": round(other, 6), "enrich_session_seconds": round(sess or 0, 1)}


def main():
    res = {name: measure(ROOT / path / "pipeline.db") for name, path in RUNS if (ROOT / path / "pipeline.db").exists()}
    L = ["# Scaling refresh: 100 -> 500 -> 10,000 (measured)", "",
         "Each row is read from that run's saved `pipeline.db` (`calls`, `validation_failures`, `text_quarantine`).",
         "Projection = measured cost per distinct text x 484,189 distinct texts (enrichment only; verify + group + memo",
         "are added in `cost/report.md`). The 100 and 500 runs used the **standard** tier; the 10k used the **Batch API**.", "",
         "| run | tier | distinct texts | requests | retries | fallbacks | out tok/text | invalid rate | quarantine rate | enrich $ | $ / text | projected enrich $ (484,189 texts, same tier) | enrich session s |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in res.items():
        L.append(f"| {name} | {m['tier']} | {m['distinct_texts']:,} | {m['enrich_requests']} | {m['retry_requests']} | "
                 f"{m['fallback_requests']} | {m['output_tokens_per_text']} | {m['invalid_rate']} | {m['quarantine_rate']} | "
                 f"{m['enrich_cost_usd']:.4f} | {m['enrich_cost_per_text_usd']:.7f} | "
                 f"**{m['enrich_cost_per_text_usd'] * FULL_DISTINCT:,.2f}** | {m['enrich_session_seconds']} |")
    L += ["", "Notes: the 100/500 invalid-rate figures predate validation-failure logging for the pilot (added 2026-10-10); "
          "the pilot's single rescue is visible as 1 retry + 1 fallback request. Fallback is OFF for the full run "
          "(decision 2026-10-10), so twice-invalid texts are quarantined."]
    (ROOT / "cost" / "scaling_refresh.md").write_text("\n".join(L) + "\n")
    (ROOT / "cost" / "scaling_refresh.json").write_text(json.dumps(res, indent=2) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
