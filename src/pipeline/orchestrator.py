"""Orchestrator: fixed stage order, chosen by CODE. Models only act inside a stage.

  1 ingest   (code)   CSV -> pipeline.db, ingestion_report.json          stop: malformed CSV / dup IDs
  2 enrich   (model)  pending distinct texts -> results / quarantine     stop: done | cap | Ctrl-C | API down
  - records  (code)   saved state -> records.jsonl (one row per source ID)
  3 verify   (model)  declared sample -> blind labels -> disagreement report (+ planted errors)
  4 group    (code+model) records -> membership.csv; model names issues only
  5 rank     (code)   records + membership -> ranking.csv, aggregates, area rollup, trends
  6 memo     (model)  bounded evidence pack -> memo.md; code renders/checks claims -> claims.csv
  - export   (code)   grading/ folder

Every stage appends start/end/status to run_log.jsonl; run_summary.json totals usage and cost per role.
Downstream stages run only if enrichment finished (no pending texts) unless --allow-partial is given,
so an incomplete classification can never silently feed the memo.
"""

import json
import time
from pathlib import Path

import yaml

from .common import ROOT, connect, write_json
from .prompts import build_enricher


class StageLog:
    def __init__(self, run_dir, run_id):
        self.path = Path(run_dir) / "run_log.jsonl"
        self.run_id = run_id

    def __call__(self, stage, fn, *a, **kw):
        t0 = time.time()
        self._write({"run_id": self.run_id, "stage": stage, "event": "start", "ts": t0})
        try:
            out = fn(*a, **kw)
        except Exception as exc:
            self._write({"run_id": self.run_id, "stage": stage, "event": "error", "ts": time.time(),
                         "seconds": round(time.time() - t0, 3), "error": f"{type(exc).__name__}: {exc}"})
            raise
        self._write({"run_id": self.run_id, "stage": stage, "event": "end", "ts": time.time(),
                     "seconds": round(time.time() - t0, 3), "result": _jsonable(out)})
        return out

    def _write(self, row):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _jsonable(x):
    try:
        json.dumps(x)
        return x
    except TypeError:
        return str(x)[:500]


def load_cfg():
    return yaml.safe_load((ROOT / "config" / "run.yaml").read_text())


def record_counts(run_dir):
    c = {"records": 0, "completed": 0, "completed_via_cache": 0, "quarantined_empty": 0,
         "quarantined_model": 0, "unprocessed": 0, "complaint_or_cancellation": 0, "needs_review": 0}
    with open(Path(run_dir) / "records.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            c["records"] += 1
            if r["status"] == "completed":
                c["completed"] += 1
                c["completed_via_cache"] += "cache_source_id" in r
                c["complaint_or_cancellation"] += r["intent"] in ("complaint", "cancellation")
                c["needs_review"] += r["needs_review"]
            elif r["reason"] == "empty_review_text":
                c["quarantined_empty"] += 1
            elif r["reason"].startswith("model_output_invalid"):
                c["quarantined_model"] += 1
            else:
                c["unprocessed"] += 1
    return c


def usage_by_role(con):
    out = {}
    for r in con.execute("""SELECT role, model, tier, COUNT(*) n, SUM(outcome='succeeded') ok, SUM(outcome='failed') failed,
                            SUM(input_tokens) i, SUM(cache_creation_input_tokens) cw, SUM(cache_read_input_tokens) cr,
                            SUM(output_tokens) o, SUM(cost_usd) usd, SUM(ended_at-started_at) call_seconds
                            FROM calls GROUP BY role, model, tier"""):
        out[f"{r['role']}|{r['model']}|{r['tier']}"] = {
            "attempts": r["n"], "succeeded": r["ok"], "failed": r["failed"], "input_tokens": r["i"],
            "cache_creation_input_tokens": r["cw"], "cache_read_input_tokens": r["cr"], "output_tokens": r["o"],
            "cost_usd": round(r["usd"], 6), "summed_call_seconds": round(r["call_seconds"], 2)}
    return out


def run_pipeline(csv_path, run_dir, client, *, allow_partial=False, use_model_names=True, limit=None,
                 max_batches=None, workers=None, progress=print, eval_dir=None, cfg=None):
    from .enrich import Enricher, pending_texts
    from .export import export
    from .group import build_membership, name_issues, write_group_outputs, default_name
    from .ingest import ingest
    from .llm import load_rates
    from .memo import write_memo
    from .rank import rank
    from .records import write_records
    from .runtime import make_llm
    from .verify import verify

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg or load_cfg()
    enr = build_enricher(cfg)
    run_id = f"run-{time.strftime('%Y%m%dT%H%M%S')}"
    log = StageLog(run_dir, run_id)
    t0 = time.time()
    db = run_dir / "pipeline.db"
    if not db.exists():
        log("ingest", ingest, csv_path, db, run_dir / "ingestion_report.json")
    con = connect(db)

    summary = log("enrich", Enricher(con, run_dir, enr, cfg, load_rates(), client).run,
                  limit=limit, max_batches=max_batches, workers=workers, progress=progress)
    pending = len(pending_texts(con, enr["label_config"]))
    log("records", write_records, con, enr["label_config"], run_dir / "records.jsonl")
    if pending and not allow_partial:
        progress(f"STOP: {pending:,} texts still pending ({summary['stop_reason']}). Downstream stages skipped; "
                 f"rerun to resume, or pass --allow-partial for a clearly-marked partial analysis.")
        _write_summary(run_dir, con, run_id, csv_path, enr, cfg, t0, partial=True)
        return {"stopped": True, "pending": pending}

    eval_dir = Path(eval_dir or ROOT / "evals" / run_dir.name)
    labels = enr["labels"]
    log("verify", verify, run_dir, con, make_llm(con, cfg, client, "verify"), cfg, labels, eval_dir)

    def group_stage():
        members, quotes = build_membership(run_dir / "records.jsonl")
        if use_model_names:
            names, called = name_issues(con, make_llm(con, cfg, client, "group"), cfg, members, quotes)
        else:
            names, called = {i: {"name": default_name(i), "description": "", "source": "default",
                                 "request_id": None} for i in members}, False
        write_group_outputs(run_dir, members, quotes, names)
        return {"issues": len(members), "members": sum(len(v) for v in members.values()), "naming_call": called}
    log("group", group_stage)

    timestamps = {r[0]: r[1] for r in con.execute("SELECT review_id, review_timestamp FROM reviews")}
    _, _, digest = log("rank", rank, run_dir, timestamps)
    counts = record_counts(run_dir)
    log("memo", write_memo, run_dir, con, make_llm(con, cfg, client, "memo"), cfg, counts)
    log("export", export, run_dir, con, csv_path)
    return _write_summary(run_dir, con, run_id, csv_path, enr, cfg, t0, partial=bool(pending), ranking_sha=digest)


def _write_summary(run_dir, con, run_id, csv_path, enr, cfg, t0, partial, ranking_sha=None):
    from .common import file_sha256
    s = {"run_id": run_id, "input_csv": str(csv_path), "input_sha256": file_sha256(csv_path),
         "label_config": enr["label_config"], "models": {k: cfg[k]["model"] for k in ("enrich", "verify", "group", "memo", "fallback")},
         "controls": cfg["controls"], "partial": partial,
         "records": record_counts(run_dir) if (Path(run_dir) / "records.jsonl").exists() else None,
         "usage_by_role": usage_by_role(con), "ranking_sha256": ranking_sha,
         "total_cost_usd": round(con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0], 6),
         "wall_clock_seconds_this_invocation": round(time.time() - t0, 2),
         "sessions": [dict(r) for r in con.execute("SELECT * FROM sessions ORDER BY started_at")]}
    write_json(Path(run_dir) / "run_summary.json", s)
    return s
