# 100-review cost & runtime report

_Generated offline from `cost/pilot_calls.jsonl` → `cost/usage.csv` × `/Users/kunaal.wadekar.bhargava/Documents/Agentic AI Assignments/Final Assignment/cost/rates.csv`. No API key or model call is used to produce this report._

## Rates used (editable: `cost/rates.csv`)

| model | tier | item | USD / 1M tokens | source | checked |
|---|---|---|---|---|---|
| claude-haiku-4-5 | batch | cache_write_5m | 0.625 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-haiku-4-5 | batch | cache_read | 0.050 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-haiku-4-5 | batch | input | 0.500 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-haiku-4-5 | batch | output | 2.500 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-haiku-4-5 | standard | cache_write_5m | 1.250 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-haiku-4-5 | standard | cache_read | 0.100 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-haiku-4-5 | standard | input | 1.000 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-haiku-4-5 | standard | output | 5.000 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-sonnet-5-5 | standard | cache_write_5m | 2.500 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-sonnet-5-5 | standard | cache_read | 0.200 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-sonnet-5-5 | standard | input | 2.000 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |
| claude-sonnet-5-5 | standard | output | 10.000 | [platform.claude.com/docs/en/ab…](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-04 |

## Measured: cold run

- Input rows: 100 · completed: 100 · failed: 0 · unique texts: 100 · result-cache hits: 0
- **Wall-clock: 83.35 s** · **API cost: $0.100972** · per 1,000 inputs: $1.0097 · per completed record: $0.001010 · throughput: 1.2 records/s
- New enrichment calls: **4**

| stage | model | tier | attempts | ok | failed | retries | fallbacks | uncached in | cache write | cache read | output | cost USD | summed call s | stage wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| enrich | claude-haiku-4-5,claude-sonnet-5-5 | standard | 4 | 4 | 0 | 1 | 1 | 3802 | 13523 | 11552 | 6828 | 0.066368 | 59.8 | 59.811 |
| verify | claude-haiku-4-5 | standard | 1 | 1 | 0 | 0 | 0 | 1791 | 0 | 0 | 323 | 0.003406 | 3.55 | 3.557 |
| group | claude-haiku-4-5 | standard | 1 | 1 | 0 | 0 | 0 | 1424 | 0 | 0 | 708 | 0.004964 | 9.09 | 9.095 |
| memo | claude-sonnet-5-5 | standard | 1 | 1 | 0 | 0 | 0 | 5377 | 0 | 0 | 1548 | 0.026234 | 10.81 | 10.823 |

## Measured: warm run

- Input rows: 100 · completed: 100 · failed: 0 · unique texts: 100 · result-cache hits: 100
- **Wall-clock: 0.04 s** · **API cost: $0.000000** · per 1,000 inputs: $0.0000 · per completed record: $0.000000 · throughput: 2500.0 records/s
- New enrichment calls: **0**

| stage | model | tier | attempts | ok | failed | retries | fallbacks | uncached in | cache write | cache read | output | cost USD | summed call s | stage wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| enrich | - | - | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0 | 0.001 |
| verify | - | - | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0 | 0.002 |
| group | - | - | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0 | 0.001 |
| memo | - | - | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0 | 0.002 |

Local compute: pipeline code runs on a laptop CPU; electricity/hardware cost is **unknown (not measured)** and is not included in API spend.

## Full-run projection (ESTIMATES)

Volume: 660,622 rows · 660,609 nonempty · 13 empty-text quarantines · 484,189 distinct nonempty texts.

```json
{
  "tier": "batch",
  "batch_size": 50,
  "workers": 4,
  "budget_usd": 150.0,
  "output_token_cap_per_review": 110,
  "verify_fraction": 0.02,
  "retry_rate_base": 0.02,
  "fallback_fraction_max": 0.002,
  "fallback_model": "claude-sonnet-5-5",
  "measured_fallback_rate": 0.01,
  "measured_cost_per_fallback_usd": 0.02073,
  "measured_per_text": {
    "input_tokens": 38.02,
    "cache_creation_input_tokens": 135.23,
    "output_tokens": 68.28,
    "cache_read_input_tokens": 115.52
  },
  "system_prompt_tokens_per_request": 6268.8,
  "summed_call_seconds_per_text": 0.598,
  "note": "Batch-tier figures are ESTIMATES (pilot used the standard tier). Batch API turnaround is asynchronous (up to 24h); sync hours shown assume the stated worker count."
}
```

| scenario | exact-text reuse | tier | texts | requests | enrich $ | verify $ | group+memo $ (once) | total API $ | est. hours (sync) | over budget? |
|---|---|---|---|---|---|---|---|---|---|---|
| base | True | batch | 484,189 | 9,684 | 151.44 | 1.65 | 0.0312 | **153.12** | 20.5 | ⚠️ **YES** |
| base | False | batch | 660,609 | 13,213 | 204.97 | 2.25 | 0.0312 | **207.25** | 28.0 | ⚠️ **YES** |
| conservative | True | batch | 484,189 | 9,684 | 155.11 | 1.65 | 0.0468 | **156.81** | 32.0 | ⚠️ **YES** |
| conservative | False | batch | 660,609 | 13,213 | 209.98 | 2.25 | 0.0468 | **212.28** | 43.6 | ⚠️ **YES** |

Budget: **$150.00** · max workers 4 · output cap 110 tokens/review · fallback cap 1% of texts (claude-sonnet-5-5); base scenario uses max(assumed, measured) rate.
