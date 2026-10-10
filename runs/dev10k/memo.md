# Decision Memo: Next-Quarter Product Priority

## Recommendation

Prioritize **billing/support**, focusing on `ISS-billing-premium_only_features` (basic features locked behind the Premium paywall). It is the highest-ranked issue tied to a named product area, with `695 [C01]` complaints, a severity sum of `1943 [C02]` (mean severity `2.795683 [C03]`), and `117 [X01]` records expressing cancellation intent. This is the largest cancellation count of any issue in the pack.

Secondary focus: `ISS-playback-crash_or_wont_open` as a reliability track, since it is the most severe large issue (see below).

## Why

- **Volume and severity:** Billing/support has `845 [X02]` complaints (`0.191915 [X03]` of all complaints) and a severity sum of `2318 [X04]`. It has the highest cancellation count of any area (`124 [X05]`) and `33 [X06]` records at severity four or higher.
- **Baseline ranking:** `ISS-billing-premium_only_features` ranks second overall, with priority score `1943 [C04]`. Only the generic `ISS-other-general_criticism` ranks above it.
- **Voice of the user:** "you can't even pick a certain song without Spotify premium" [review d146397f-4a94-4980-be00-ae9212eca3a7]; "app is kind of no use if u dont buy the premium" [review aa79fe52-744f-4cab-adb2-b99aca01fdae].

## Alternatives considered

- **Usability:** The largest named-area severity sum (`2637 [X07]`), driven mostly by `ISS-usability-ads` (`1333 [C05]`) and `ISS-usability-shuffle_and_song_choice`. Its mean severity is low (`2.271318 [X08]`), with only `6 [X09]` records at severity four or higher, and fewer cancellation signals (`78 [X10]`). It is a close second, and ads are plausibly linked to the paywall theme, so the two may be worth considering together. Usability ranks below billing/support because its complaints are less severe and carry fewer cancellation signals.
- **Playback:** Fewer complaints (`666 [X11]`), but the highest severity among the large areas (mean `3.138138 [X12]`, with `214 [X13]` records at severity four or higher). `ISS-playback-crash_or_wont_open` (severity sum `849 [C06]`) deserves engineering attention, but its total severity sum (`2090 [X14]`) is below billing/support. Representative quote: "App closing automatically when playing songs" [review 40abba11-6cce-4eff-ae74-9ec4579550e0].
- **Access:** Very severe per complaint (mean `3.640845 [X15]`) but small, with `142 [X16]` complaints and a severity sum of `517 [X17]`. `ISS-access-login_failure` is worth a targeted fix, but it is too narrow to anchor the quarter.
- **Other (generic):** `1237 [X18]` complaints, the largest count, mostly `ISS-other-general_criticism` (severity sum `2431 [C07]`). Its mean severity is low (`1.997535 [C08]`) and it has no records at severity four or higher. Quotes such as "Aweful" [review 14a5b8b7-eae3-444b-8c1a-223ea468f980] give no actionable product direction, so we do not recommend it as a target; it indicates general sentiment only.

## Limitations

- Reviews are self-selected, historical Google Play reviews and do not represent all users.
- Cancellation language is expressed intent, not observed churn. The pack has no revenue, plan-tier, or causal information.
- Labels are model-generated with measured error. Of `10000` records, `9976` completed, `24` were quarantined after model failures, and `1348` are flagged as needing review. Findings should be read with that uncertainty in mind.
- Coordinated or repeated posting may inflate some counts.
- The "premium-only features" label may mix product-design complaints with billing complaints; the right fix (messaging, free-tier limits, or packaging) is a leadership decision that this data cannot settle.

## What to watch

1. Complaint count and cancellation-intent count for `ISS-billing-premium_only_features` in subsequent reviews.
2. Severity-four-plus complaints in playback and access, to confirm reliability does not worsen while effort is directed elsewhere.

---
_Claim trace (code-generated):_ C01 = `ISS-billing-premium_only_features` complaint_count 695; C02 = `ISS-billing-premium_only_features` severity_sum 1943; C03 = `ISS-billing-premium_only_features` mean_severity 2.795683; C04 = `ISS-billing-premium_only_features` priority_score 1943; C05 = `ISS-usability-ads` severity_sum 1333; C06 = `ISS-playback-crash_or_wont_open` severity_sum 849; C07 = `ISS-other-general_criticism` severity_sum 2431; C08 = `ISS-other-general_criticism` mean_severity 1.997535
