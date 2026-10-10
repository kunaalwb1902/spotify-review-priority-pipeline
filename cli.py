"""Pipeline entry point. Each stage reads saved inputs and writes saved outputs.

Full pipeline on any CSV (PAID - calls the API):
    python cli.py run --csv data/raw/spotify_reviews_18months.csv --run runs/full

Regenerate ranking from saved outputs (NO model calls, no API key needed):
    python cli.py rank --run runs/full

Individual stages: ingest | enrich | records | verify | group | rank | memo | export | status
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def _client(cfg):
    import anthropic
    from dotenv import load_dotenv
    from pipeline.common import ROOT
    load_dotenv(ROOT / ".env")
    return anthropic.Anthropic(max_retries=0, timeout=cfg["controls"]["request_timeout_seconds"])


def _cfg(args):
    from pipeline.orchestrator import load_cfg
    cfg = load_cfg()
    if getattr(args, "cap", None) is not None:
        cfg["controls"]["spend_cap_usd"] = args.cap
    if getattr(args, "batch_size", None) is not None:
        cfg["enrich"]["batch_size"] = args.batch_size
    return cfg


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, help_, csv=False, paid=False):
        p = sub.add_parser(name, help=("(PAID) " if paid else "") + help_)
        p.add_argument("--run", required=True, help="run directory, e.g. runs/full")
        if csv:
            p.add_argument("--csv", required=True, help="input CSV with the six source columns")
        if paid:
            p.add_argument("--cap", type=float, help="override spend cap in USD")
        return p

    add("ingest", "Stage 1: read CSV, hash rows, profile, quarantine empty texts", csv=True)
    p = add("enrich", "Stage 2: classify pending distinct texts", paid=True)
    p.add_argument("--limit", type=int, help="only the first N pending distinct texts (dev/smoke runs)")
    p.add_argument("--max-batches", type=int, help="stop after N batches (interruption demo)")
    p.add_argument("--workers", type=int, help="override config/run.yaml controls.workers")
    p.add_argument("--batch-size", type=int, help="reviews per request (1-50)")
    p.add_argument("--transport", choices=["sync", "batch"], default="sync", help="batch = Message Batches API (50%% off, async)")
    p.add_argument("--chunk-requests", type=int, default=500, help="batch transport: requests per submitted batch")
    p.add_argument("--max-chunks", type=int, help="batch transport: stop after submitting N batches")
    p.add_argument("--poll-seconds", type=int, default=60)
    add("records", "write records.jsonl (one final record per source ID)")
    add("verify", "Stage 3: blind re-label of the declared sample + planted errors", paid=True)
    p = add("group", "Stage 4: issue membership (code) + issue names (model)", paid=True)
    p.add_argument("--no-model", action="store_true", help="use code-generated names; zero calls")
    add("rank", "Stage 5: ranking from saved records + membership (never calls a model)")
    add("memo", "Stage 6: memo from bounded evidence pack; code checks claims", paid=True)
    add("export", "write the grading/ folder", csv=True)
    add("status", "record accounting and usage for a run (no API)")
    p = add("run", "all stages in order on an input CSV", csv=True, paid=True)
    p.add_argument("--limit", type=int)
    p.add_argument("--max-batches", type=int)
    p.add_argument("--workers", type=int)
    p.add_argument("--batch-size", type=int)
    p.add_argument("--allow-partial", action="store_true", help="run downstream stages even if texts are pending")

    args = parser.parse_args()
    run = Path(args.run)

    if args.cmd == "ingest":
        from pipeline.ingest import ingest
        report = ingest(args.csv, run / "pipeline.db", run / "ingestion_report.json")
        c = report["counts"]
        print(f"records={c['records']:,} nonempty={c['nonempty_to_classify']:,} "
              f"quarantined_empty={c['empty_review_text_quarantined']} "
              f"distinct_texts={c['distinct_nonempty_texts']:,} missing_app_version={c['missing_app_version']:,} "
              f"manifest={report['manifest_check']['status']} ({report['seconds']}s)")
        return

    if args.cmd == "run":
        from pipeline.orchestrator import run_pipeline
        cfg = _cfg(args)
        out = run_pipeline(args.csv, run, _client(cfg), allow_partial=args.allow_partial, limit=args.limit,
                           max_batches=args.max_batches, workers=args.workers, cfg=cfg)
        print({k: out.get(k) for k in ("run_id", "partial", "total_cost_usd", "ranking_sha256", "stopped", "pending")})
        return

    from pipeline.common import connect
    from pipeline.prompts import build_enricher
    cfg = _cfg(args)
    enr = build_enricher(cfg)
    con = connect(run / "pipeline.db")

    if args.cmd == "enrich":
        from pipeline.llm import load_rates
        if args.transport == "batch":
            from pipeline.batch_enrich import BatchEnricher
            BatchEnricher(con, run, enr, cfg, load_rates(), _client(cfg)).run_batch(
                limit=args.limit, chunk_requests=args.chunk_requests, max_chunks=args.max_chunks,
                poll_seconds=args.poll_seconds)
        else:
            from pipeline.enrich import Enricher
            Enricher(con, run, enr, cfg, load_rates(), _client(cfg)).run(
                limit=args.limit, max_batches=args.max_batches, workers=args.workers)

    elif args.cmd == "records":
        from pipeline.records import write_records
        print(write_records(con, enr["label_config"], run / "records.jsonl"))

    elif args.cmd == "verify":
        from pipeline.common import ROOT
        from pipeline.runtime import make_llm
        from pipeline.verify import verify
        s = verify(run, con, make_llm(con, cfg, _client(cfg), "verify"), cfg, enr["labels"], ROOT / "evals" / run.name)
        print({k: s[k] for k in ("topic_agreement", "intent_agreement", "severity_mae", "disagreements",
                                 "planted_errors", "new_verify_calls")})

    elif args.cmd == "group":
        from pipeline.group import build_membership, default_name, name_issues, write_group_outputs
        from pipeline.runtime import make_llm
        members, quotes = build_membership(run / "records.jsonl")
        if args.no_model:
            names = {i: {"name": default_name(i), "description": "", "source": "default", "request_id": None}
                     for i in members}
        else:
            names, _ = name_issues(con, make_llm(con, cfg, _client(cfg), "group"), cfg, members, quotes)
        write_group_outputs(run, members, quotes, names)
        print(f"issues={len(members)} memberships={sum(map(len, members.values()))}")

    elif args.cmd == "rank":
        from pipeline.rank import rank
        ts = {r[0]: r[1] for r in con.execute("SELECT review_id, review_timestamp FROM reviews")}
        rows, areas, digest = rank(run, ts)
        print(f"{'rank':>4}  {'issue_id':<48} {'count':>8} {'sev_sum':>8} {'mean':>9}")
        for r in rows[:15]:
            print(f"{r['rank']:>4}  {r['issue_id']:<48} {r['complaint_count']:>8} {r['severity_sum']:>8} {r['mean_severity']:>9}")
        print(f"... {len(rows)} issues | ranking.csv sha256 {digest}")

    elif args.cmd == "memo":
        from pipeline.memo import write_memo
        from pipeline.orchestrator import record_counts
        from pipeline.runtime import make_llm
        print(write_memo(run, con, make_llm(con, cfg, _client(cfg), "memo"), cfg, record_counts(run)))

    elif args.cmd == "export":
        from pipeline.export import export
        g, n = export(run, con, args.csv)
        print(f"wrote {g} ({n} calls)")

    elif args.cmd == "status":
        lc = enr["label_config"]
        q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
        has = lambda t: q("SELECT COUNT(*) FROM sqlite_master WHERE name=?", t)
        total = q("SELECT COUNT(*) FROM reviews")
        empty = q("SELECT COUNT(*) FROM reviews WHERE status='quarantined'")
        done = q("""SELECT COUNT(*) FROM reviews r JOIN results x ON x.text_sha256=r.text_sha256
                    AND x.run_config=? WHERE r.status!='quarantined'""", lc) if has("results") else 0
        failed = q("""SELECT COUNT(*) FROM reviews r JOIN text_quarantine x ON x.text_sha256=r.text_sha256
                      AND x.label_config=? WHERE r.status!='quarantined'""", lc) if has("text_quarantine") else 0
        print(f"label_config: {lc}")
        print(f"records {total:,} = completed {done:,} + quarantined(empty) {empty} "
              f"+ quarantined(model) {failed:,} + pending {total - done - empty - failed:,}")
        if has("calls"):
            for r in con.execute("SELECT role, outcome, COUNT(*), SUM(input_tokens), SUM(cache_read_input_tokens), "
                                 "SUM(output_tokens), ROUND(SUM(cost_usd),4) FROM calls GROUP BY role, outcome"):
                print("  calls", tuple(r))


if __name__ == "__main__":
    main()
