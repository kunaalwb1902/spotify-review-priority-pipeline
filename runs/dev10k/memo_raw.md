# Decision Memo: Next-Quarter Product Priority

## Recommendation

Prioritize **billing/support**, focusing on `ISS-billing-premium_only_features` (basic features locked behind the Premium paywall). It is the highest-ranked issue tied to a named product area, with `{ISS-billing-premium_only_features.complaint_count}` complaints, a severity sum of `{ISS-billing-premium_only_features.severity_sum}` (mean severity `{ISS-billing-premium_only_features.mean_severity}`), and `{ISS-billing-premium_only_features.cancellation_count}` records expressing cancellation intent. This is the largest cancellation count of any issue in the pack.

Secondary focus: `ISS-playback-crash_or_wont_open` as a reliability track, since it is the most severe large issue (see below).

## Why

- **Volume and severity:** Billing/support has `{AREA:billing_support.complaint_count}` complaints (`{AREA:billing_support.share_of_complaints}` of all complaints) and a severity sum of `{AREA:billing_support.severity_sum}`. It has the highest cancellation count of any area (`{AREA:billing_support.cancellation_count}`) and `{AREA:billing_support.severity_4_plus}` records at severity four or higher.
- **Baseline ranking:** `ISS-billing-premium_only_features` ranks second overall, with priority score `{ISS-billing-premium_only_features.priority_score}`. Only the generic `ISS-other-general_criticism` ranks above it.
- **Voice of the user:** "you can't even pick a certain song without Spotify premium" [review d146397f-4a94-4980-be00-ae9212eca3a7]; "app is kind of no use if u dont buy the premium" [review aa79fe52-744f-4cab-adb2-b99aca01fdae].

## Alternatives considered

- **Usability:** The largest named-area severity sum (`{AREA:usability.severity_sum}`), driven mostly by `ISS-usability-ads` (`{ISS-usability-ads.severity_sum}`) and `ISS-usability-shuffle_and_song_choice`. Its mean severity is low (`{AREA:usability.mean_severity}`), with only `{AREA:usability.severity_4_plus}` records at severity four or higher, and fewer cancellation signals (`{AREA:usability.cancellation_count}`). It is a close second, and ads are plausibly linked to the paywall theme, so the two may be worth considering together. Usability ranks below billing/support because its complaints are less severe and carry fewer cancellation signals.
- **Playback:** Fewer complaints (`{AREA:playback.complaint_count}`), but the highest severity among the large areas (mean `{AREA:playback.mean_severity}`, with `{AREA:playback.severity_4_plus}` records at severity four or higher). `ISS-playback-crash_or_wont_open` (severity sum `{ISS-playback-crash_or_wont_open.severity_sum}`) deserves engineering attention, but its total severity sum (`{AREA:playback.severity_sum}`) is below billing/support. Representative quote: "App closing automatically when playing songs" [review 40abba11-6cce-4eff-ae74-9ec4579550e0].
- **Access:** Very severe per complaint (mean `{AREA:access.mean_severity}`) but small, with `{AREA:access.complaint_count}` complaints and a severity sum of `{AREA:access.severity_sum}`. `ISS-access-login_failure` is worth a targeted fix, but it is too narrow to anchor the quarter.
- **Other (generic):** `{AREA:other.complaint_count}` complaints, the largest count, mostly `ISS-other-general_criticism` (severity sum `{ISS-other-general_criticism.severity_sum}`). Its mean severity is low (`{ISS-other-general_criticism.mean_severity}`) and it has no records at severity four or higher. Quotes such as "Aweful" [review 14a5b8b7-eae3-444b-8c1a-223ea468f980] give no actionable product direction, so we do not recommend it as a target; it indicates general sentiment only.

## Limitations

- Reviews are self-selected, historical Google Play reviews and do not represent all users.
- Cancellation language is expressed intent, not observed churn. The pack has no revenue, plan-tier, or causal information.
- Labels are model-generated with measured error. Of `{RUN.records}` records, `{RUN.completed}` completed, `{RUN.quarantined_model}` were quarantined after model failures, and `{RUN.needs_review}` are flagged as needing review. Findings should be read with that uncertainty in mind.
- Coordinated or repeated posting may inflate some counts.
- The "premium-only features" label may mix product-design complaints with billing complaints; the right fix (messaging, free-tier limits, or packaging) is a leadership decision that this data cannot settle.

## What to watch

1. Complaint count and cancellation-intent count for `ISS-billing-premium_only_features` in subsequent reviews.
2. Severity-four-plus complaints in playback and access, to confirm reliability does not worsen while effort is directed elsewhere.