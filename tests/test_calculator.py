"""Calculator properties the instructors check (COST_CALCULATOR.md section 5). Offline, synthetic usage."""

import copy
import csv
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cost"))
import calculator as calc  # noqa: E402


def _calls():
    base = dict(model="claude-haiku-4-5", tier="standard", usage_known=True, attempt=1, outcome="succeeded")
    calls = []
    for run in ("cold", "warm"):
        if run == "cold":
            calls += [dict(base, pilot_run=run, request_id="e1", role="enrich", purpose="first", input_tokens=1400,
                           cache_creation_input_tokens=5776, cache_read_input_tokens=0, output_tokens=3300,
                           started_at=0, ended_at=20),
                      dict(base, pilot_run=run, request_id="e2", role="enrich", purpose="first", input_tokens=1400,
                           cache_creation_input_tokens=0, cache_read_input_tokens=5776, output_tokens=3300,
                           started_at=20, ended_at=40),
                      dict(base, pilot_run=run, request_id="v1", role="verify", purpose="blind_relabel",
                           input_tokens=3000, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                           output_tokens=300, started_at=40, ended_at=45),
                      dict(base, pilot_run=run, request_id="g1", role="group", purpose="name_issues",
                           input_tokens=2000, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                           output_tokens=600, started_at=45, ended_at=50),
                      dict(base, pilot_run=run, request_id="m1", role="memo", purpose="write_memo",
                           model="claude-sonnet-5-5", input_tokens=4000, cache_creation_input_tokens=0,
                           cache_read_input_tokens=0, output_tokens=2500, started_at=50, ended_at=80)]
    return calls


META = {"cold": {"wall_clock_seconds": 85.0, "input_rows": 100, "completed_records": 100, "failed_records": 0,
                 "unique_texts": 100, "result_cache_hits": 0, "verify_sample": 10, "stage_seconds": {}},
        "warm": {"wall_clock_seconds": 1.2, "input_rows": 100, "completed_records": 100, "failed_records": 0,
                 "unique_texts": 100, "result_cache_hits": 100, "verify_sample": 10, "stage_seconds": {}}}


def _args(**kw):
    a = Namespace(budget=150.0, workers=4, batch_size=50, tier="batch", output_cap=110, retry_rate=0.02,
                  fallback_fraction=0.002, fallback_model="claude-sonnet-5-5", verify_fraction=0.02,
                  hours_est=24.0, pilot_verify_n=10)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _doubled_rates(tmp_path):
    out = tmp_path / "rates2.csv"
    with open(ROOT / "cost" / "rates.csv", newline="") as f, open(out, "w", newline="") as g:
        r = csv.DictReader(f)
        w = csv.DictWriter(g, fieldnames=r.fieldnames)
        w.writeheader()
        for row in r:
            row["price_usd_per_million_tokens"] = str(float(row["price_usd_per_million_tokens"]) * 2)
            w.writerow(row)
    return out


def _measure(rates_path):
    calls = _calls()
    priced, unpriced = calc.price_usage(calc.usage_rows(calls), calc.load_rates(rates_path))
    assert not unpriced
    return calc.measured(calls, priced, META)


def test_item_cost_is_units_times_price():
    rates = calc.load_rates(ROOT / "cost" / "rates.csv")
    priced, _ = calc.price_usage(calc.usage_rows(_calls()), rates)
    e1_out = next(p for p in priced if p["request_id"] == "e1" and p["usage_field"] == "output_tokens")
    assert abs(e1_out["item_cost_usd"] - 3300 * 5.00 / 1e6) < 1e-12
    cw = next(p for p in priced if p["request_id"] == "e1" and p["usage_field"] == "cache_creation_input_tokens")
    assert abs(cw["item_cost_usd"] - 5776 * 1.25 / 1e6) < 1e-12


def test_doubling_rates_doubles_api_spend_only(tmp_path):
    m1, m2 = _measure(ROOT / "cost" / "rates.csv"), _measure(_doubled_rates(tmp_path))
    for run in ("cold", "warm"):
        assert abs(m2[run]["api_cost_usd"] - 2 * m1[run]["api_cost_usd"]) < 1e-9
        assert m2[run]["wall_clock_seconds"] == m1[run]["wall_clock_seconds"]          # time unchanged
        for s in m1[run]["stages"]:
            assert m2[run]["stages"][s]["summed_call_seconds"] == m1[run]["stages"][s]["summed_call_seconds"]


def test_projection_does_not_change_measured_results():
    rates = calc.load_rates(ROOT / "cost" / "rates.csv")
    meas = _measure(ROOT / "cost" / "rates.csv")
    snapshot = copy.deepcopy(meas)
    old = dict(calc.FULL)
    try:
        calc.FULL["distinct_nonempty"] *= 3
        p_big = calc.projection(meas, rates, _args(budget=1.0))
    finally:
        calc.FULL.clear()
        calc.FULL.update(old)
    assert meas == snapshot
    assert all(s["exceeds_budget"] for s in p_big["scenarios"])                        # budget warning fires


def test_warm_run_shows_zero_new_enrichment_calls():
    assert _measure(ROOT / "cost" / "rates.csv")["warm"]["new_enrichment_calls"] == 0


def test_memo_cost_counted_once_not_per_review():
    rates = calc.load_rates(ROOT / "cost" / "rates.csv")
    meas = _measure(ROOT / "cost" / "rates.csv")
    p = calc.projection(meas, rates, _args())
    memo_group = meas["cold"]["stages"]["memo"]["cost_usd"] + meas["cold"]["stages"]["group"]["cost_usd"]
    base = next(s for s in p["scenarios"] if s["scenario"] == "base" and s["exact_text_reuse"])
    assert abs(base["fixed_group_memo_usd"] - round(memo_group, 4)) < 1e-9


def test_reuse_is_cheaper_than_no_reuse():
    rates = calc.load_rates(ROOT / "cost" / "rates.csv")
    p = calc.projection(_measure(ROOT / "cost" / "rates.csv"), rates, _args())
    by = {(s["scenario"], s["exact_text_reuse"]): s for s in p["scenarios"]}
    assert by[("base", True)]["total_api_usd"] < by[("base", False)]["total_api_usd"]
    assert by[("base", True)]["total_api_usd"] < by[("conservative", True)]["total_api_usd"]
