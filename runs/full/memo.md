# Decision Memo: Next-Quarter Product Priority

## Recommendation

Prioritize **billing/support**, with the premium paywall issue `ISS-billing-premium_only_features` as the lead item. Basic playback controls (shuffle, previous track, seeking) are reported as locked behind premium. It is the highest-ranked issue tied to a specific product area: `47824 [C01]` complaints, severity sum `132048 [C02]`, mean severity `2.761124 [C03]`. It also carries `8764 [X01]` cancellation-intent records, the most among the named issues.

## Why

Of `288755` complaint or cancellation records, the billing/support area accounts for `56634 [X02]` complaints (`0.196132 [X03]` share) and a severity sum of `155719 [X04]`. Its mean severity of `2.749567 [X05]` is higher than usability's, and its cancellation count (`9438 [X06]`) is far larger than any other area's. It has `2683 [X07]` records at severity four or above.

Representative voices:
- "it requires only premium users and force users to pay user subscription" [review c917e047-2d4b-41c5-ad41-bc9355b6d6e4]
- "wouldnt even let us shuffle, play previous track, repeat a single song back to back, etc. For non premium users." [review e7747485-42c5-4bb9-b1e2-1b4044eedb5a]

## Alternatives considered

- **Usability:** Largest named area by severity sum (`168108 [X08]`), driven by `ISS-usability-ads` (`82940 [C04]`). Its mean severity is low (`2.290830 [X09]`) and it has few severe records (`449 [X10]`). Its cancellation count is much smaller than billing/support's. Its total is slightly larger than billing/support's, so this is a close call. I rank it second because severity and cancellation intent are weaker. Ads are arguably tied to the same free-versus-premium tension, so the two may interact.
- **Playback:** Highest severity among the large areas (mean `3.160062 [X11]`, `13696 [X12]` severe records), led by `ISS-playback-crash_or_wont_open` (`14364 [C05]` complaints). Its volume (`40147 [X13]`) and severity sum (`126867 [X14]`) are smaller than billing/support's. It is a strong reliability candidate and a good second-line investment.
- **Access:** Most severe per complaint (mean `3.705441 [X15]`), mainly `ISS-access-login_failure`, but small in volume (`9336 [X16]` complaints, `0.032332 [X17]` share). A targeted fix may be worthwhile, but it does not lead on total volume or severity sum.
- **Other (generic):** `ISS-other-general_criticism` ranks first overall (`166035 [C06]` severity sum), and the `other` area has the largest complaint share (`0.291711 [X18]`). It is not actionable: it is generic dissatisfaction (e.g., "It's really bad app not working properly nonsense" [review 3f462947-3f4b-4d38-ada4-7dab21c0055e]), with low mean severity (`1.990586 [X19]`) and very few severe records (`9 [X20]`). It cannot be assigned to a team or fix, so I exclude it from the recommendation.

## Limitations

- Reviews are self-selected, historical Google Play reviews and do not represent all users.
- Cancellation language is expressed intent, not observed churn.
- There is no revenue, plan-tier, or causal information; the evidence cannot say what a fix would change.
- Issue and severity labels are model-generated with measured error (see the evaluation). `1068` records were quarantined by the model, `13` were empty, and `86714` are flagged as needing review.
- Coordinated or repeated posting is possible and was not ruled out.
- The paywall complaints may reflect product policy as much as a defect, so the "fix" may be a business decision rather than an engineering one.

## What to watch

1. Complaint volume and cancellation-intent counts for `ISS-billing-premium_only_features` in subsequent review periods.
2. Whether severe records in `ISS-playback-crash_or_wont_open` and `ISS-access-login_failure` stay stable, since these are the next candidates.

---
_Claim trace (code-generated):_ C01 = `ISS-billing-premium_only_features` complaint_count 47824; C02 = `ISS-billing-premium_only_features` severity_sum 132048; C03 = `ISS-billing-premium_only_features` mean_severity 2.761124; C04 = `ISS-usability-ads` severity_sum 82940; C05 = `ISS-playback-crash_or_wont_open` complaint_count 14364; C06 = `ISS-other-general_criticism` severity_sum 166035
