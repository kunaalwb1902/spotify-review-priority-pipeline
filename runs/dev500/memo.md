# Decision Memo: Where to Focus Next Quarter

## Recommendation

Prioritize **billing/support**, with the first target being `ISS-billing-premium_only_features` (core features locked behind Premium). Within this area, the issue has `42 [C01]` complaints, a severity sum of `112 [C02]`, a mean severity of `2.666667 [C03]`, and `4 [X01]` cancellation mentions.

This is a judgement call, and the case is close. By raw severity sum, usability leads the areas (`140 [X02]`), but that total is driven by ads, which have low per-complaint severity. Billing/support has a higher mean severity (`2.666667 [X03]` versus `2.222222 [X04]`) and more severity-four-plus complaints (`3 [X05]` versus `0 [X06]`).

## Why

- **Volume and severity:** Billing/support has `48 [X07]` complaints (`0.200000 [X08]` of all complaints) and a severity sum of `128 [X09]`. `ISS-billing-premium_only_features` ranks second overall, just behind the generic `ISS-other-general_criticism`.
- **Representative voices:** "you can't even pick a certain song without Spotify premium" [review d146397f-4a94-4980-be00-ae9212eca3a7]; "the new update has made everthing premium and so usless" [review 21915fcd-9a35-48ba-b277-6296c4154603].
- **Link to usability:** Skip limits and song choice also surface in `ISS-usability-shuffle_and_song_choice`. Free-tier restrictions may therefore be a shared root of complaints in both areas. This is a hypothesis, not a causal finding.

## Alternatives considered

- **Usability:** Largest area by complaint count (`63 [X10]`) and severity sum, led by `ISS-usability-ads` (`33 [C04]` complaints, mean severity `2.090909 [C05]`). Complaints are frequent but low in severity, with `0 [X06]` at severity four or above. This is the strongest runner-up, and ads could be addressed together with the paywall work.
- **Playback:** Fewer complaints (`37 [X11]`) but the most severe broad area (mean `3.135135 [X12]`, `13 [X13]` at severity four or above). Key issues are `ISS-playback-stops_or_pauses` and `ISS-playback-crash_or_wont_open`. Its severity sum (`116 [X14]`) is below billing/support, but it is a strong candidate for a targeted reliability fix, for example "the app is just crashing since the latest update" [review 4a806587-0dde-4fb4-b4f1-f5c7d197c927].
- **Access:** Very small (`9 [X15]` complaints) but the highest mean severity (`4.000000 [X16]`). `ISS-access-login_failure` is worth a low-cost fix, but its volume is too small to justify a quarter of effort.
- **Other (generic):** `ISS-other-general_criticism` ranks first overall (`57 [C06]` complaints, mean severity `2.000000 [C07]`) but is not tied to a product area and gives no actionable target. Example: "I hate application" [review 1db044db-d184-4335-877c-d8fc364b282e]. Some of it likely overlaps with paywall and ad frustration, but the pack cannot show that. Treat it as a sentiment indicator rather than a work item.

## Limitations

- Reviews are self-selected, historical Google Play reviews and do not represent all users.
- Cancellation language is expressed intent, not observed churn.
- The pack contains no revenue, plan-tier, or causal information.
- Labels are model-generated with measured error. Of `500` records, `500` completed, and `56` are flagged for review. `0` were quarantined for model failure, `0` for empty text, and `0` remain unprocessed.
- Only `240` records are complaints or cancellations, so category counts are small.
- Repeated or coordinated posting is possible and was not ruled out.

## What to watch

1. Complaint volume and severity for `ISS-billing-premium_only_features` and `ISS-usability-shuffle_and_song_choice` in new reviews after any change.
2. Cancellation-intent mentions in billing/support and playback, and whether severity-four-plus complaints in playback decline.

---
_Claim trace (code-generated):_ C01 = `ISS-billing-premium_only_features` complaint_count 42; C02 = `ISS-billing-premium_only_features` severity_sum 112; C03 = `ISS-billing-premium_only_features` mean_severity 2.666667; C04 = `ISS-usability-ads` complaint_count 33; C05 = `ISS-usability-ads` mean_severity 2.090909; C06 = `ISS-other-general_criticism` complaint_count 57; C07 = `ISS-other-general_criticism` mean_severity 2.000000
