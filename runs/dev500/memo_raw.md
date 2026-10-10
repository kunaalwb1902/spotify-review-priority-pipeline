# Decision Memo: Where to Focus Next Quarter

## Recommendation

Prioritize **billing/support**, with the first target being `ISS-billing-premium_only_features` (core features locked behind Premium). Within this area, the issue has `{ISS-billing-premium_only_features.complaint_count}` complaints, a severity sum of `{ISS-billing-premium_only_features.severity_sum}`, a mean severity of `{ISS-billing-premium_only_features.mean_severity}`, and `{ISS-billing-premium_only_features.cancellation_count}` cancellation mentions.

This is a judgement call, and the case is close. By raw severity sum, usability leads the areas (`{AREA:usability.severity_sum}`), but that total is driven by ads, which have low per-complaint severity. Billing/support has a higher mean severity (`{AREA:billing_support.mean_severity}` versus `{AREA:usability.mean_severity}`) and more severity-four-plus complaints (`{AREA:billing_support.severity_4_plus}` versus `{AREA:usability.severity_4_plus}`).

## Why

- **Volume and severity:** Billing/support has `{AREA:billing_support.complaint_count}` complaints (`{AREA:billing_support.share_of_complaints}` of all complaints) and a severity sum of `{AREA:billing_support.severity_sum}`. `ISS-billing-premium_only_features` ranks second overall, just behind the generic `ISS-other-general_criticism`.
- **Representative voices:** "you can't even pick a certain song without Spotify premium" [review d146397f-4a94-4980-be00-ae9212eca3a7]; "the new update has made everthing premium and so usless" [review 21915fcd-9a35-48ba-b277-6296c4154603].
- **Link to usability:** Skip limits and song choice also surface in `ISS-usability-shuffle_and_song_choice`. Free-tier restrictions may therefore be a shared root of complaints in both areas. This is a hypothesis, not a causal finding.

## Alternatives considered

- **Usability:** Largest area by complaint count (`{AREA:usability.complaint_count}`) and severity sum, led by `ISS-usability-ads` (`{ISS-usability-ads.complaint_count}` complaints, mean severity `{ISS-usability-ads.mean_severity}`). Complaints are frequent but low in severity, with `{AREA:usability.severity_4_plus}` at severity four or above. This is the strongest runner-up, and ads could be addressed together with the paywall work.
- **Playback:** Fewer complaints (`{AREA:playback.complaint_count}`) but the most severe broad area (mean `{AREA:playback.mean_severity}`, `{AREA:playback.severity_4_plus}` at severity four or above). Key issues are `ISS-playback-stops_or_pauses` and `ISS-playback-crash_or_wont_open`. Its severity sum (`{AREA:playback.severity_sum}`) is below billing/support, but it is a strong candidate for a targeted reliability fix, for example "the app is just crashing since the latest update" [review 4a806587-0dde-4fb4-b4f1-f5c7d197c927].
- **Access:** Very small (`{AREA:access.complaint_count}` complaints) but the highest mean severity (`{AREA:access.mean_severity}`). `ISS-access-login_failure` is worth a low-cost fix, but its volume is too small to justify a quarter of effort.
- **Other (generic):** `ISS-other-general_criticism` ranks first overall (`{ISS-other-general_criticism.complaint_count}` complaints, mean severity `{ISS-other-general_criticism.mean_severity}`) but is not tied to a product area and gives no actionable target. Example: "I hate application" [review 1db044db-d184-4335-877c-d8fc364b282e]. Some of it likely overlaps with paywall and ad frustration, but the pack cannot show that. Treat it as a sentiment indicator rather than a work item.

## Limitations

- Reviews are self-selected, historical Google Play reviews and do not represent all users.
- Cancellation language is expressed intent, not observed churn.
- The pack contains no revenue, plan-tier, or causal information.
- Labels are model-generated with measured error. Of `{RUN.records}` records, `{RUN.completed}` completed, and `{RUN.needs_review}` are flagged for review. `{RUN.quarantined_model}` were quarantined for model failure, `{RUN.quarantined_empty}` for empty text, and `{RUN.unprocessed}` remain unprocessed.
- Only `{RUN.complaint_or_cancellation}` records are complaints or cancellations, so category counts are small.
- Repeated or coordinated posting is possible and was not ruled out.

## What to watch

1. Complaint volume and severity for `ISS-billing-premium_only_features` and `ISS-usability-shuffle_and_song_choice` in new reviews after any change.
2. Cancellation-intent mentions in billing/support and playback, and whether severity-four-plus complaints in playback decline.