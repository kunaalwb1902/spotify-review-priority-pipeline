"""Write the standardized grading/ folder (GRADING_CONTRACT.md) from saved run artifacts. Code only."""

import gzip
import json
import shutil
from pathlib import Path

from .common import checker, file_sha256

LARGE = 50_000_000      # gzip JSONL files above ~50 MB


def export_calls(con, path):
    rows = con.execute("SELECT * FROM calls ORDER BY started_at, rowid")
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            uncached, cw, cr = r["input_tokens"], r["cache_creation_input_tokens"], r["cache_read_input_tokens"]
            event = {
                "request_id": r["request_id"], "role": r["role"], "review_ids": json.loads(r["review_ids"]),
                "model": r["model"], "outcome": r["outcome"], "label_config": r["label_config"],
                # input_tokens = ALL billed input (uncached + cache write + cache read); breakdown below.
                "input_tokens": uncached + cw + cr, "output_tokens": r["output_tokens"],
                "input_tokens_uncached": uncached, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
                "usage_known": bool(r["usage_known"]), "cost_usd": round(r["cost_usd"], 8), "tier": r["tier"],
                "attempt": r["attempt"], "purpose": r["purpose"], "session_id": r["session_id"],
                "error": r["error"], "started_at": r["started_at"], "ended_at": r["ended_at"],
            }
            if r["role"] == "enrich":
                event["phase"] = r["phase"]
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
    return n


def export(run_dir, con, csv_path, grading_dir=None):
    run_dir = Path(run_dir)
    g = Path(grading_dir or run_dir / "grading")
    g.mkdir(parents=True, exist_ok=True)
    checker.write_json(g / "run.json", {
        "version": checker.VERSION, "analysis_count": con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0],
        "analysis_sha256": file_sha256(csv_path), "classification_input_fields": ["review_text"],
        "allow_multi_issue": False})
    checker.write_json(g / "ingestion.json", checker.profile(csv_path))     # the provided helper, as required
    for name in ("membership.csv", "ranking.csv", "claims.csv", "checkpoint_before.json", "checkpoint_after.json"):
        if (run_dir / name).exists():
            shutil.copyfile(run_dir / name, g / name)
    for name in ("records.jsonl",):
        src = run_dir / name
        for stale in (g / name, g / (name + ".gz")):
            stale.unlink(missing_ok=True)
        if src.stat().st_size > LARGE:
            with open(src, "rb") as fi, gzip.open(g / (name + ".gz"), "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
        else:
            shutil.copyfile(src, g / name)
    (g / "calls.jsonl").unlink(missing_ok=True)
    (g / "calls.jsonl.gz").unlink(missing_ok=True)
    n = export_calls(con, run_dir / "calls.jsonl")
    if (run_dir / "calls.jsonl").stat().st_size > LARGE:
        with open(run_dir / "calls.jsonl", "rb") as fi, gzip.open(g / "calls.jsonl.gz", "wb") as fo:
            shutil.copyfileobj(fi, fo)
    else:
        shutil.copyfile(run_dir / "calls.jsonl", g / "calls.jsonl")
    return g, n
