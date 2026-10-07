"""100-review cost & runtime calculator (COST_CALCULATOR.md).

OFFLINE REPLAY (default; no API key, no network, no model calls):
    python cost/calculator.py                       # recompute from cost/usage.csv x cost/rates.csv
    python cost/calculator.py --rates my_rates.csv  # e.g. doubled prices -> API spend doubles, time unchanged
    python cost/calculator.py --budget 120 --workers 4 --fallback-fraction 0.02

PAID PILOT (explicit, separate command; spends money):
    python cost/calculator.py pilot --confirm-paid  # cold run on cost_100.csv (empty cache, 1 worker), then warm rerun

The measured section is computed ONLY from saved files (pilot_calls.jsonl -> usage.csv); projections are
computed afterwards from those numbers plus editable assumptions, and never write back to measurements.
"""

import argparse
import csv
import json
import math
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

COST = Path(__file__).resolve().parent
ROOT = COST.parent
USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")

FULL = {"rows": 660_622, "nonempty": 660_609, "empty": 13, "distinct_nonempty": 484_189}
COST100_SHA = "c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e"


# ------------------------------------------------------------------------------------------------
# Measurement files
# ------------------------------------------------------------------------------------------------

def load_rates(path):
    rates = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rates[(r["model"], r["tier"], r["usage_field"])] = {
                "per_token": float(r["price_usd_per_million_tokens"]) / 1_000_000,
                "per_million": float(r["price_usd_per_million_tokens"]), "source": r["source_url"],
                "checked": r["checked_date"], "item": r["billing_item"]}
    return rates


def load_calls(path=COST / "pilot_calls.jsonl"):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def usage_rows(calls):
    """One row per (call, billing item): the auditable mapping from charged item to call log."""
    rows = []
    for c in calls:
        for field in USAGE_FIELDS:
            units = c.get(field, 0) or 0
            if units:
                rows.append({"pilot_run": c["pilot_run"], "request_id": c["request_id"], "stage": c["role"],
                             "model": c["model"], "tier": c["tier"], "usage_field": field, "billed_units": units,
                             "unit": "token", "usage_known": c["usage_known"]})
    return rows


def write_usage_csv(rows, path=COST / "usage.csv"):
    cols = ["pilot_run", "request_id", "stage", "model", "tier", "usage_field", "billed_units", "unit", "usage_known"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def read_usage_csv(path=COST / "usage.csv"):
    with open(path, newline="", encoding="utf-8") as f:
        return [{**r, "billed_units": int(r["billed_units"]), "usage_known": r["usage_known"] == "True"}
                for r in csv.DictReader(f)]


def price_usage(usage, rates):
    """item_cost = billed_units x price_per_unit. Missing rate -> reported as unpriced, never silently 0."""
    priced, unpriced = [], []
    for u in usage:
        r = rates.get((u["model"], u["tier"], u["usage_field"]))
        if r is None:
            unpriced.append(u)
            continue
        priced.append({**u, "price_per_token": r["per_token"], "item_cost_usd": u["billed_units"] * r["per_token"]})
    return priced, unpriced


# ------------------------------------------------------------------------------------------------
# Measured report
# ------------------------------------------------------------------------------------------------

def measured(calls, priced, meta):
    out = {}
    for run in ("cold", "warm"):
        rc = [c for c in calls if c["pilot_run"] == run]
        rp = [p for p in priced if p["pilot_run"] == run]
        m = meta.get(run, {})
        stages = {}
        for stage in ("enrich", "verify", "group", "memo"):
            sc = [c for c in rc if c["role"] == stage]
            sp = [p for p in rp if p["stage"] == stage]
            tokens = defaultdict(int)
            for p in sp:
                tokens[p["usage_field"]] += p["billed_units"]
            stages[stage] = {
                "models": sorted({c["model"] for c in sc}), "tiers": sorted({c["tier"] for c in sc}),
                "attempts": len(sc), "succeeded": sum(c["outcome"] == "succeeded" for c in sc),
                "failed": sum(c["outcome"] == "failed" for c in sc),
                "retries": sum(c.get("attempt", 1) > 1 or c.get("purpose") == "retry_invalid" for c in sc),
                "fallbacks": sum(c.get("purpose") == "fallback" for c in sc),
                "usage_missing": sum(not c["usage_known"] for c in sc),
                "tokens": dict(tokens), "cost_usd": sum(p["item_cost_usd"] for p in sp),     # full precision; round on display
                "summed_call_seconds": round(sum(c["ended_at"] - c["started_at"] for c in sc), 2),
                "stage_wall_seconds": m.get("stage_seconds", {}).get(stage),
            }
        total = sum(s["cost_usd"] for s in stages.values())
        wall = m.get("wall_clock_seconds")
        inputs = m.get("input_rows", 100)
        completed = m.get("completed_records")
        out[run] = {"stages": stages, "api_cost_usd": total, "wall_clock_seconds": wall,
                    "input_rows": inputs, "completed_records": completed, "failed_records": m.get("failed_records"),
                    "unique_texts": m.get("unique_texts"), "result_cache_hits": m.get("result_cache_hits"),
                    "new_enrichment_calls": stages["enrich"]["attempts"],
                    "cost_per_1000_inputs_usd": total / inputs * 1000 if inputs else None,
                    "cost_per_completed_record_usd": total / completed if completed else None,
                    "throughput_records_per_second": round(completed / wall, 3) if completed and wall else None}
    return out


# ------------------------------------------------------------------------------------------------
# Projection (estimates only; never modifies measured numbers)
# ------------------------------------------------------------------------------------------------

def projection(meas, rates, a):
    cold = meas["cold"]
    e = cold["stages"]["enrich"]
    texts = cold["unique_texts"] or 100
    model = (e["models"] or ["claude-haiku-4-5"])[0]
    per_text_tokens = {k: v / texts for k, v in e["tokens"].items()}
    # Separate the per-request fixed prompt (cache write/read) from per-text variable tokens.
    req = max(1, e["succeeded"])
    sys_tokens = (e["tokens"].get("cache_creation_input_tokens", 0) + e["tokens"].get("cache_read_input_tokens", 0)) / req
    var = {"input_tokens": e["tokens"].get("input_tokens", 0) / texts,
           "output_tokens": e["tokens"].get("output_tokens", 0) / texts}
    secs_per_text = (e["summed_call_seconds"] / texts) if texts else 0

    def price(model, tier, field):
        r = rates.get((model, tier, field))
        return r["per_token"] if r else None

    def enrich_cost(n_texts, tier, retry_rate, fallback_frac):
        n_req = math.ceil(n_texts / a.batch_size)
        p = lambda f: price(model, tier, f)
        if None in (p("input_tokens"), p("output_tokens"), p("cache_read_input_tokens")):
            return None
        # each request reads the cached system prompt; ~1 cache write per 5-min window per worker (assumed)
        cache_writes = max(a.workers, math.ceil(a.hours_est * 12) * a.workers)
        sys_cost = (n_req * sys_tokens * p("cache_read_input_tokens")
                    + cache_writes * sys_tokens * (p("cache_creation_input_tokens") or p("input_tokens")))
        var_cost = n_texts * (var["input_tokens"] * p("input_tokens") + var["output_tokens"] * p("output_tokens"))
        retry_cost = retry_rate * var_cost
        fb_model = a.fallback_model
        fb = rates.get((fb_model, "standard", "output_tokens"))
        fb_cost = 0.0
        if fb:
            per_fb = (sys_tokens * rates[(fb_model, "standard", "input_tokens")]["per_token"]
                      + var["input_tokens"] * rates[(fb_model, "standard", "input_tokens")]["per_token"]
                      + var["output_tokens"] * fb["per_token"] + 200 * fb["per_token"])
            fb_cost = fallback_frac * n_texts * per_fb
        return sys_cost + var_cost + retry_cost + fb_cost

    v = cold["stages"]["verify"]
    v_texts = max(1, a.pilot_verify_n)
    verify_per_text = v["cost_usd"] / v_texts
    fixed = cold["stages"]["group"]["cost_usd"] + cold["stages"]["memo"]["cost_usd"]   # once per run, NOT per review

    scenarios = []
    for name, tier, retry, fb, slow in (("base", a.tier, a.retry_rate, a.fallback_fraction, 1.0),
                                        ("conservative", a.tier, a.retry_rate * 3, min(0.01, a.fallback_fraction * 3) if a.fallback_fraction else 0.01, 1.5)):
        for reuse in (True, False):
            n = FULL["distinct_nonempty"] if reuse else FULL["nonempty"]
            ec = enrich_cost(n, tier, retry, fb)
            vc = verify_per_text * a.verify_fraction * n * (1.0 if tier == "standard" else 0.5)
            total = None if ec is None else ec + vc + fixed * (1.5 if name == "conservative" else 1.0)
            hours = n * secs_per_text * slow * (1 + retry) / max(1, a.workers) / 3600
            scenarios.append({"scenario": name, "exact_text_reuse": reuse, "tier": tier, "texts_to_classify": n,
                              "requests": math.ceil(n / a.batch_size), "retry_rate": retry, "fallback_fraction": fb,
                              "enrich_usd": None if ec is None else round(ec, 2), "verify_usd": round(vc, 2),
                              "fixed_group_memo_usd": round(fixed * (1.5 if name == "conservative" else 1.0), 4),
                              "total_api_usd": None if total is None else round(total, 2),
                              "est_hours_sync": round(hours, 1), "workers": a.workers,
                              "exceeds_budget": None if total is None else total > a.budget})
    return {"assumptions": {"tier": a.tier, "batch_size": a.batch_size, "workers": a.workers, "budget_usd": a.budget,
                            "output_token_cap_per_review": a.output_cap, "verify_fraction": a.verify_fraction,
                            "retry_rate_base": a.retry_rate, "fallback_fraction_max": a.fallback_fraction,
                            "fallback_model": a.fallback_model,
                            "measured_per_text": {k: round(v, 2) for k, v in per_text_tokens.items()},
                            "system_prompt_tokens_per_request": round(sys_tokens, 1),
                            "summed_call_seconds_per_text": round(secs_per_text, 4),
                            "note": "Batch-tier figures are ESTIMATES (pilot used the standard tier). Batch API turnaround is "
                                    "asynchronous (up to 24h); sync hours shown assume the stated worker count."},
            "volume": FULL, "scenarios": scenarios}


# ------------------------------------------------------------------------------------------------
# Report
# ------------------------------------------------------------------------------------------------

def _fmt(x, d):
    return "n/a" if x is None else f"{x:.{d}f}"


def render(meas, proj, rates, unpriced, a):
    L = ["# 100-review cost & runtime report", "",
         f"_Generated offline from `cost/pilot_calls.jsonl` → `cost/usage.csv` × `{a.rates}`. "
         "No API key or model call is used to produce this report._", ""]
    L += ["## Rates used (editable: `cost/rates.csv`)", "", "| model | tier | item | USD / 1M tokens | source | checked |",
          "|---|---|---|---|---|---|"]
    for (m, t, f), r in sorted(rates.items()):
        L.append(f"| {m} | {t} | {r['item']} | {r['per_million']:.3f} | [{r['source'].split('//')[1][:30]}…]({r['source']}) | {r['checked']} |")
    if unpriced:
        L += ["", f"**Unpriced usage rows: {len(unpriced)}** — no matching rate; shown as UNKNOWN, not $0."]
    for run in ("cold", "warm"):
        m = meas[run]
        L += ["", f"## Measured: {run} run", "",
              f"- Input rows: {m['input_rows']} · completed: {m['completed_records']} · failed: {m['failed_records']} · "
              f"unique texts: {m['unique_texts']} · result-cache hits: {m['result_cache_hits']}",
              f"- **Wall-clock: {m['wall_clock_seconds']} s** · **API cost: ${m['api_cost_usd']:.6f}** · "
              f"per 1,000 inputs: ${_fmt(m['cost_per_1000_inputs_usd'], 4)} · per completed record: ${_fmt(m['cost_per_completed_record_usd'], 6)} · "
              f"throughput: {m['throughput_records_per_second']} records/s",
              f"- New enrichment calls: **{m['new_enrichment_calls']}**", "",
              "| stage | model | tier | attempts | ok | failed | retries | fallbacks | uncached in | cache write | cache read | output | cost USD | summed call s | stage wall s |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for s, d in m["stages"].items():
            t = d["tokens"]
            L.append(f"| {s} | {','.join(d['models']) or '-'} | {','.join(d['tiers']) or '-'} | {d['attempts']} | {d['succeeded']} | "
                     f"{d['failed']} | {d['retries']} | {d['fallbacks']} | {t.get('input_tokens', 0)} | "
                     f"{t.get('cache_creation_input_tokens', 0)} | {t.get('cache_read_input_tokens', 0)} | "
                     f"{t.get('output_tokens', 0)} | {d['cost_usd']:.6f} | {d['summed_call_seconds']} | {d['stage_wall_seconds']} |")
    L += ["", "Local compute: pipeline code runs on a laptop CPU; electricity/hardware cost is **unknown (not measured)** "
          "and is not included in API spend.", "",
          "## Full-run projection (ESTIMATES)", "",
          f"Volume: {FULL['rows']:,} rows · {FULL['nonempty']:,} nonempty · {FULL['empty']} empty-text quarantines · "
          f"{FULL['distinct_nonempty']:,} distinct nonempty texts.", "",
          "```json", json.dumps(proj["assumptions"], indent=2), "```", "",
          "| scenario | exact-text reuse | tier | texts | requests | enrich $ | verify $ | group+memo $ (once) | total API $ | est. hours (sync) | over budget? |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in proj["scenarios"]:
        flag = "⚠️ **YES**" if s["exceeds_budget"] else ("unknown" if s["exceeds_budget"] is None else "no")
        L.append(f"| {s['scenario']} | {s['exact_text_reuse']} | {s['tier']} | {s['texts_to_classify']:,} | {s['requests']:,} | "
                 f"{s['enrich_usd']} | {s['verify_usd']} | {s['fixed_group_memo_usd']} | **{s['total_api_usd']}** | "
                 f"{s['est_hours_sync']} | {flag} |")
    L += ["", f"Budget: **${a.budget:.2f}** · max workers {a.workers} · output cap {a.output_cap} tokens/review · "
          f"fallback ≤ {a.fallback_fraction:.0%} of texts ({a.fallback_model}).", ""]
    return "\n".join(L)


def offline(a):
    rates = load_rates(a.rates)
    calls = load_calls()
    if not (COST / "usage.csv").exists():
        write_usage_csv(usage_rows(calls))
    usage = read_usage_csv()
    priced, unpriced = price_usage(usage, rates)
    meta = json.loads((COST / "pilot_meta.json").read_text())
    meas = measured(calls, priced, meta)
    a.pilot_verify_n = meta.get("cold", {}).get("verify_sample", 10)
    proj = projection(meas, rates, a)
    report = render(meas, proj, rates, unpriced, a)
    out = Path(a.out)
    out.write_text(report)
    (COST / "projection.json").write_text(json.dumps({"measured": meas, "projection": proj}, indent=2) + "\n")
    print(report)
    print(f"\n[offline replay] wrote {out} and cost/projection.json - no API calls made")


# ------------------------------------------------------------------------------------------------
# Paid pilot (explicit)
# ------------------------------------------------------------------------------------------------

def pilot(a):
    if not a.confirm_paid:
        sys.exit("Refusing to spend money without --confirm-paid.")
    sys.path.insert(0, str(ROOT / "src"))
    import anthropic
    from dotenv import load_dotenv
    from pipeline.common import connect, file_sha256
    from pipeline.orchestrator import load_cfg, run_pipeline
    load_dotenv(ROOT / ".env")
    src = ROOT / "data" / "raw" / "cost_100.csv"
    assert file_sha256(src) == COST100_SHA, "cost_100.csv does not match manifest checksum"
    run_dir = ROOT / "runs" / "pilot100"
    if run_dir.exists():
        if not a.fresh:
            sys.exit(f"{run_dir} exists. A cold run needs an empty result cache: pass --fresh to archive it.")
        shutil.move(str(run_dir), str(run_dir) + f".archived-{time.strftime('%Y%m%dT%H%M%S')}")
    cfg = load_cfg()
    cfg["controls"]["workers"] = 1
    cfg["controls"]["spend_cap_usd"] = a.pilot_cap
    client = anthropic.Anthropic(max_retries=0, timeout=cfg["controls"]["request_timeout_seconds"])
    meta = {}
    for phase in ("cold", "warm"):
        t0 = time.time()
        stamp = time.time()
        out = run_pipeline(src, run_dir, client, cfg=cfg, eval_dir=ROOT / "evals" / "pilot100")
        wall = time.time() - t0
        stage_secs = {}
        for line in open(run_dir / "run_log.jsonl"):
            ev = json.loads(line)
            if ev["event"] == "end" and ev["ts"] >= stamp:
                stage_secs[ev["stage"]] = ev["seconds"]
        con = connect(run_dir / "pipeline.db")
        new_calls = con.execute("SELECT COUNT(*) FROM calls WHERE started_at >= ?", (stamp,)).fetchone()[0]
        rc = out["records"]
        meta[phase] = {"wall_clock_seconds": round(wall, 2), "stage_seconds": stage_secs, "input_rows": rc["records"],
                       "completed_records": rc["completed"], "failed_records": rc["quarantined_model"] + rc["unprocessed"],
                       "unique_texts": con.execute("SELECT COUNT(DISTINCT text_sha256) FROM reviews WHERE status!='quarantined'").fetchone()[0],
                       "result_cache_hits": 0 if phase == "cold" else rc["completed"],
                       "new_model_calls": new_calls, "started_at": stamp,
                       "verify_sample": json.loads((ROOT / "evals" / "pilot100" / "verifier_summary.json").read_text())["declared_sample"]["sampled"]}
        rows = con.execute("SELECT * FROM calls WHERE started_at >= ? ORDER BY started_at", (stamp,)).fetchall()
        with open(COST / "pilot_calls.jsonl", "a" if phase == "warm" else "w", encoding="utf-8") as f:
            for r in rows:
                d = dict(r)
                d["pilot_run"] = phase
                d["review_ids"] = json.loads(d["review_ids"])
                d["usage_known"] = bool(d["usage_known"])
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print(f"[{phase}] wall {wall:.1f}s, new model calls {new_calls}")
    shutil.copyfile(run_dir / "records.jsonl", COST / "pilot_records.jsonl")
    (COST / "pilot_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    write_usage_csv(usage_rows(load_calls()))
    print("Pilot saved. Now run the offline replay:  python cost/calculator.py")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", default="offline", choices=["offline", "pilot"])
    ap.add_argument("--rates", default=str(COST / "rates.csv"))
    ap.add_argument("--out", default=str(COST / "report.md"))
    ap.add_argument("--budget", type=float, default=150.0, help="spending limit for projections (USD)")
    ap.add_argument("--workers", type=int, default=4, help="max concurrent workers assumed for the full run")
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--tier", default="batch", choices=["standard", "batch"], help="tier assumed for the full run")
    ap.add_argument("--output-cap", type=int, default=110, help="output-token cap per review (max_tokens basis)")
    ap.add_argument("--retry-rate", type=float, default=0.02, help="fraction of texts re-sent after invalid output")
    ap.add_argument("--fallback-fraction", type=float, default=0.002, help="fraction sent to the stronger model (cap 0.01)")
    ap.add_argument("--fallback-model", default="claude-sonnet-5-5")
    ap.add_argument("--verify-fraction", type=float, default=0.02)
    ap.add_argument("--hours-est", type=float, default=24.0, help="run duration used for cache-write count")
    ap.add_argument("--confirm-paid", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="archive an existing runs/pilot100 for a true cold start")
    ap.add_argument("--pilot-cap", type=float, default=2.0, help="hard spend cap for the paid pilot (USD)")
    a = ap.parse_args()
    if a.mode == "pilot":
        pilot(a)
    else:
        offline(a)


if __name__ == "__main__":
    main()
