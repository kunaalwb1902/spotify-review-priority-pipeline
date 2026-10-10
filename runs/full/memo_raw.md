# Decision Memo: Next-Quarter Product Priority

## Recommendation

Prioritize **billing/support**, with the premium paywall issue `ISS-billing-premium_only_features` as the lead item. Basic playback controls (shuffle, previous track, seeking) are reported as locked behind premium. It is the highest-ranked issue tied to a specific product area: `{ISS-billing-premium_only_features.complaint_count}` complaints, severity sum `{ISS-billing-premium_only_features.severity_sum}`, mean severity `{ISS-billing-premium_only_features.mean_severity}`. It also carries `{ISS-billing-premium_only_features.cancellation_count}` cancellation-intent records, the most among the named issues.

## Why

Of `{RUN.complaint_or_cancellation}` complaint or cancellation records, the billing/support area accounts for `{AREA:billing_support.complaint_count}` complaints (`{AREA:billing_support.share_of_complaints}` share) and a severity sum of `{AREA:billing_support.severity_sum}`. Its mean severity of `{AREA:billing_support.mean_severity}` is higher than usability's, and its cancellation count (`{AREA:billing_support.cancellation_count}`) is far larger than any other area's. It has `{AREA:billing_support.severity_4_plus}` records at severity four or above.

Representative voices:
- "it requires only premium users and force users to pay user subscription" [review c917e047-2d4b-41c5-ad41-bc9355b6d6e4]
- "wouldnt even let us shuffle, play previous track, repeat a single song back to back, etc. For non premium users." [review e7747485-42c5-4bb9-b1e2-1b4044eedb5a]

## Alternatives considered

- **Usability:** Largest named area by severity sum (`{AREA:usability.severity_sum}`), driven by `ISS-usability-ads` (`{ISS-usability-ads.severity_sum}`). Its mean severity is low (`{AREA:usability.mean_severity}`) and it has few severe records (`{AREA:usability.severity_4_plus}`). Its cancellation count is much smaller than billing/support's. Its total is slightly larger than billing/support's, so this is a close call. I rank it second because severity and cancellation intent are weaker. Ads are arguably tied to the same free-versus-premium tension, so the two may interact.
- **Playback:** Highest severity among the large areas (mean `{AREA:playback.mean_severity}`, `{AREA:playback.severity_4_plus}` severe records), led by `ISS-playback-crash_or_wont_open` (`{ISS-playback-crash_or_wont_open.complaint_count}` complaints). Its volume (`{AREA:playback.complaint_count}`) and severity sum (`{AREA:playback.severity_sum}`) are smaller than billing/support's. It is a strong reliability candidate and a good second-line investment.
- **Access:** Most severe per complaint (mean `{AREA:access.mean_severity}`), mainly `ISS-access-login_failure`, but small in volume (`{AREA:access.complaint_count}` complaints, `{AREA:access.share_of_complaints}` share). A targeted fix may be worthwhile, but it does not lead on total volume or severity sum.
- **Other (generic):** `ISS-other-general_criticism` ranks first overall (`{ISS-other-general_criticism.severity_sum}` severity sum), and the `other` area has the largest complaint share (`{AREA:other.share_of_complaints}`). It is not actionable: it is generic dissatisfaction (e.g., "It's really bad app not working properly nonsense" [review 3f462947-3f4b-4d38-ada4-7dab21c0055e]), with low mean severity (`{AREA:other.mean_severity}`) and very few severe records (`{AREA:other.severity_4_plus}`). It cannot be assigned to a team or fix, so I exclude it from the recommendation.

## Limitations

- Reviews are self-selected, historical Google Play reviews and do not represent all users.
- Cancellation language is expressed intent, not observed churn.
- There is no revenue, plan-tier, or causal information; the evidence cannot say what a fix would change.
- Issue and severity labels are model-generated with measured error (see the evaluation). `{RUN.quarantined_model}` records were quarantined by the model, `{RUN.quarantined_empty}` were empty, and `{RUN.needs_review}` are flagged as needing review.
- Coordinated or repeated posting is possible and was not ruled out.
- The paywall complaints may reflect product policy as much as a defect, so the "fix" may be a business decision rather than an engineering one.

## What to watch

1. Complaint volume and cancellation-intent counts for `ISS-billing-premium_only_features` in subsequent review periods.
2. Whether severe records in `ISS-playback-crash_or_wont_open` and `ISS-access-login_failure` stay stable, since these are the next candidates.