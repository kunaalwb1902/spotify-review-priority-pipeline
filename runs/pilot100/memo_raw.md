# Decision Memo: Next-Quarter Product Priorities

## Recommendation

Prioritize **usability**, specifically `ISS-usability-ads` and `ISS-usability-shuffle_and_song_choice`. Usability has the largest complaint volume and severity total of any named area: {AREA:usability.complaint_count} complaints, severity sum {AREA:usability.severity_sum}, and a {AREA:usability.share_of_complaints} share of complaints. It also has {AREA:usability.cancellation_count} expressed cancellation intents.

Within it, `ISS-usability-ads` has {ISS-usability-ads.complaint_count} complaints and priority score {ISS-usability-ads.priority_score}. `ISS-usability-shuffle_and_song_choice` has {ISS-usability-shuffle_and_song_choice.complaint_count} complaints, a mean severity of {ISS-usability-shuffle_and_song_choice.mean_severity}, and {ISS-usability-shuffle_and_song_choice.cancellation_count} cancellation intent.

## Why

The pack covers {RUN.completed} completed records, of which {RUN.complaint_or_cancellation} are complaints or cancellations. Ranking is by priority score (severity sum). Usability's area total is larger than that of any other named area. Its issues are also the most concrete and addressable ones in the ranking.

Representative voices:
- "The ads are so loud" [review ab67a772-c3b7-42ba-9b80-8d21d3857018]
- "We can't even go back few second to play music?" [review 9e5f9745-f8be-48d3-b9de-4d118507d94a]

## Alternatives considered

- **Billing/support:** Its area severity sum is {AREA:billing_support.severity_sum} across {AREA:billing_support.complaint_count} complaints. It is driven almost entirely by `ISS-billing-premium_only_features` (priority {ISS-billing-premium_only_features.priority_score}), which ranks first as a single issue. But it is a complaint about the paywall boundary, which is a business-model decision rather than a defect, and the area has {AREA:billing_support.cancellation_count} cancellation intents. It is a close runner-up, and the right call if leadership is willing to revisit the free/Premium feature split. Note that the shuffle/skip-limit issue in usability is closely related to that same boundary.
- **Playback:** It has fewer complaints ({AREA:playback.complaint_count}) and a lower severity sum ({AREA:playback.severity_sum}), but higher mean severity ({AREA:playback.mean_severity}) and {AREA:playback.severity_4_plus} severity-four-plus complaints. `ISS-playback-crash_or_wont_open` (priority {ISS-playback-crash_or_wont_open.priority_score}) and `ISS-playback-stops_or_pauses` deserve a targeted stability fix, but their volume is small.
- **Access:** `ISS-access-login_failure` is the most severe by mean ({ISS-access-login_failure.mean_severity}), but it has only {ISS-access-login_failure.complaint_count} complaints. It merits a bug-level fix rather than a quarter of focus.
- **Other (generic):** `ISS-other-general_criticism` has {ISS-other-general_criticism.complaint_count} complaints and priority {ISS-other-general_criticism.priority_score}, tied with the top issue. It is not actionable: quotes like "Very bad app" [review 8b6e028a-5f63-4011-9841-cbbade4830eb] name no product problem. It is excluded from the recommendation, but its size suggests some dissatisfaction is not captured by specific issues.

## Limitations

- Reviews are self-selected, historical Google Play reviews and may not represent all users.
- Cancellation language is expressed intent, not observed churn.
- The pack has no revenue, plan-tier, or causal information.
- Labels are model-generated with measured error. {RUN.needs_review} records are flagged for review, and {RUN.unprocessed} are unprocessed.
- Coordinated or repeated posting is possible and cannot be ruled out.
- The sample is small, so differences between areas are modest and the ranking could shift with more data.

## What to watch

- Share of new reviews mentioning ads or skip limits, relative to the baseline for `ISS-usability-ads`.
- Whether login and crash complaints (`ISS-access-login_failure`, `ISS-playback-crash_or_wont_open`) rise, since they are low-volume but severe.