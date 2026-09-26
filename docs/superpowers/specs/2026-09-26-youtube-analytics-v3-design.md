# YouTube Analytics V3 Design

## Goal

Keep Wacky Dramas planning lean while retaining high-fidelity YouTube analytics for explicit deep analysis.

## Repository boundaries

- `youtube-workflow`: creative state plus three derived analytics contracts: detailed summary, compact planner projection, and discovery index. `pipeline.build_context()` is the only context writer and tolerates missing or invalid analytics.
- `production-runtime`: all OAuth, YouTube Data/Analytics/Reporting API calls, retention checkpoint policy, aggregation, and optimistic cross-repository writes.
- `youtube-analytics-data`: private append-oriented JSON/CSV storage and small manifests only; no workflows, executable code, OAuth, or secrets.

## Data flow

The runtime captures current Data API statistics and optional Analytics API breakdowns into one immutable realtime snapshot. It discovers Reporting API report types, creates missing non-system jobs, downloads each report once, and stores source CSV with metadata. It captures per-video retention once near 72 hours and once near seven days. Existing planner snapshots are copied once into a legacy warehouse folder.

The runtime derives a detailed v3 summary and a compact soft-evidence projection. It writes both plus a small index to `youtube-workflow`, then invokes the planner's deterministic context builder. The builder owns story cards/backgrounds and embeds only the compact projection as `analytics_summary`.

## Failure and concurrency model

Data API/OAuth and valid repository authentication are required for an analytics run. Unsupported optional Analytics reports, unavailable demographics/retention, pending Reporting reports, and individual optional report failures become warnings. Normal planning never calls YouTube or the warehouse and continues with the last valid projection or no analytics.

Both private-state writes compare the current main SHA before creating a fast-forward commit. A stable collection timestamp makes retries idempotent. Reporting report IDs and retention checkpoints are persisted in simple manifests.

## Compatibility

`PLANNING.md`, creative schemas, winner counts, narration/render/upload behavior, publishing slots, and minute validation remain unchanged. Scheduled planning stays at 12 winners and manual planning at one winner.
