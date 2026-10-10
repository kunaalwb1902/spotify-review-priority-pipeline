# Scaling refresh: 100 -> 500 -> 10,000 (measured)

Each row is read from that run's saved `pipeline.db` (`calls`, `validation_failures`, `text_quarantine`).
Projection = measured cost per distinct text x 484,189 distinct texts (enrichment only; verify + group + memo
are added in `cost/report.md`). The 100 and 500 runs used the **standard** tier; the 10k used the **Batch API**.

| run | tier | distinct texts | requests | retries | fallbacks | out tok/text | invalid rate | quarantine rate | enrich $ | $ / text | projected enrich $ (484,189 texts, same tier) | enrich session s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pilot100 (cost_100.csv) | standard | 100 | 4 | 1 | 1 | 68.3 | None | 0.0 | 0.0664 | 0.0006637 | **321.35** | 59.8 |
| dev500 (checkpoint_500.csv) | standard | 479 | 13 | 2 | 1 | 67.6 | 0.0042 | 0.0 | 0.2188 | 0.0004567 | **221.14** | 133.3 |
| dev10k (analysis_10000.csv) | batch | 8,448 | 171 | 2 | 0 | 69.7 | 0.0073 | 0.0028 | 1.8308 | 0.0002167 | **104.93** | 249.6 |

Notes: the 100/500 invalid-rate figures predate validation-failure logging for the pilot (added 2026-10-10); the pilot's single rescue is visible as 1 retry + 1 fallback request. Fallback is OFF for the full run (decision 2026-10-10), so twice-invalid texts are quarantined.
