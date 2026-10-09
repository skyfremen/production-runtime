# Zodiac analytics parity design

Implement Drama's analytics v3 collection and planner feedback for Wacky Astrology. Preserve youtube-workflow and youtube-analytics-data; shared runtime changes keep Drama defaults identical.

## Evidence from current Drama
- Runtime Analytics workflow is externally dispatched, uses exec credentials, and serializes collection. No repository cron or analytics warehouse workflows.
- Collector selects completed result video IDs published in the last 30 days. Data API supplies current views/likes/comments/duration; Analytics API supplies engaged views, watch duration/percentage, shares/subscribers and audience/source/device breakdowns. Unsupported optional reports become warnings.
- Reporting API discovers supported channel/playlist report types, creates jobs and downloads immutable CSV plus metadata. Pending first reports are distinguished from stale jobs after 48 hours.
- Snapshots form approximate 2h/6h/24h/72h/7d checkpoints; retention curves are retried in bounded 72h/7d windows. Collection every six hours does not guarantee a two-hour observation. Missing metrics remain null.
- Passive private warehouse holds raw/realtime/retention/manifests. Runtime writes detailed summary, <=16KB planner projection and discovery index, then asks the private planner to rebuild context. Exact write allowlists and three retries protect concurrent main commits.
- Planner stages: cold_start until >=30 24h and >=15 72h observations; early_learning until >=100 24h, >=50 72h and >=20 7d; established thereafter. Pattern minimums 5/8/12. Planning uses saved compact signals and never queries APIs.
- Drama compact pattern ranking uses median views, although PLANNING prioritizes opening EVR and downstream retention. Traffic-source engaged/views is a ratio, not YouTube Studio's stayed-to-watch measure. Raw bulk CSV remains discoverable, not fully joined into creative decisions.

## Architecture and boundaries
Add optional immutable analytics profile configuration to existing runtime collector, with unchanged Drama defaults. Zodiac profile pins planner skyfremen/zodiac-workflow, warehouse skyfremen/zodiac-analytics-data, za IDs, Zodiac secrets and @WackyAstrology. Validate authenticated channel against the handle and request channel IDs before analytics requests or Reporting job creation. Require verified scheduled/published results and exclude abandoned/cancelled IDs and future slots.

Reuse HTTP, pagination, report discovery/download, retention collection, bounded retries, allowlisted writes and context builder. Separate Zodiac workflow uses exec secrets and independent concurrency; workflow_dispatch only. No upload/render invocation.

Zodiac adapter maps immutable request winners to four native formats and objective headline-word-count buckets. Do not invent Drama categories, lead genders, story tones or unrecorded hook classifications. Retain complete rows in stored creative observations for analysis and bounded examples in planner signals. Add engagement counters, mature Shorts-source ratio, watch percentage/duration, view thresholds and per-format comparisons. Never call engaged/views Studio stayed-to-watch. Supported creative patterns require >=minimum_pattern_sample at 72h or 7d; early observations remain diagnostic. Rank supported patterns using matched Shorts-source ratio, then retention, exposing actual supporting counts, never raw views alone. Separate adequately sampled below-mature-cohort response/retention groups as weak patterns; never overlap supported and weak lists. Scope Zodiac retention summaries to currently eligible video IDs.

Planner tolerates absent/malformed/oversized analytics and retains structural context. Compact projection <=16000 bytes; analytics embedding budget <=100000 bytes (existing recent/pending list cards are preserved even if the base context itself exceeds that budget). Context owns embedding and refreshes on planner-analytics changes. Analytics never changes slots, scores, winner schemas or uploads.

## Deployment and readiness
Create a private passive zodiac-analytics-data repository on main with schema/report-jobs/collection-state manifests. Existing connector cannot create repositories or dispatch workflows; finish code and tests first, then obtain browser fallback authorization for those concrete setup actions. ZODIAC_STATE_TOKEN must grant contents write to planner and warehouse; Zodiac OAuth token must include youtube.readonly and yt-analytics.readonly (plus existing youtube.upload for publishing). Existing secret values remain private. Initial collection, remote CI and actual dataset availability must be checked separately; no fixture data is published as channel evidence.

## Validation
Test default Drama behavior, Zodiac ID/channel/repository separation, verified/future/abandoned result filtering, cold start and mature pattern gates, null and unavailable metrics, compact bounds, context fallback, reporting/retention reuse and remote retry/write scopes. Run both full test suites and runtime self/contract tests. Validate CI after commits; report setup or permission blockers honestly.

## Observed current data and API references
The inspected Drama projection at 2026-10-09T17:30:33Z analyzed 546 videos with 539/527/438 observed 24h/72h/7d checkpoints. Its warehouse had 20 active report-type jobs and retention checkpoints for 159 video IDs. These are observations of Drama, never seed data for Zodiac.

- [Analytics API authorization](https://developers.google.com/youtube/analytics/reference/)
- [Analytics metric definitions](https://developers.google.com/youtube/analytics/metrics)
- [Reporting job lifecycle](https://developers.google.com/youtube/reporting/v1/reports)
