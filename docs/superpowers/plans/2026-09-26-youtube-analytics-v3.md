# YouTube Analytics V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a private passive analytics warehouse and v3 derived analytics while leaving normal Wacky Dramas planning materially unchanged.

**Architecture:** `production-runtime` collects and derives; `youtube-analytics-data` stores raw/high-fidelity evidence; `youtube-workflow` stores only detailed derived, compact planner, index, and merged context contracts. Optimistic GitHub commits and stable collection IDs make retries safe.

**Tech Stack:** Python standard library, YouTube Data API v3, YouTube Analytics API v2, YouTube Reporting API v1, JSON/CSV, GitHub Git Data API, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-26-youtube-analytics-v3-design.md`

## Global Constraints

- Analytics schema version is `3`.
- Normal planning reads only `PLANNING.md` and `content/context.json`.
- Scheduled/manual winner counts remain `12`/`1`.
- Publishing minutes remain `00, 10, 20, 30, 40, 50`; 24 slots remain configured.
- `analytics.yml` remains `workflow_dispatch` only.
- Optional API capabilities warn and continue; creative planning never depends on analytics freshness.
- No database, service, queue, new scheduler, raw public analytics, or executable warehouse code.

## Review Focus

- Missing/invalid/stale planner projection must not break context generation.
- Reporting discovery may return deprecated, system-managed, duplicate, or unsupported report types.
- Concurrent main updates must never be force-overwritten.
- Retention queries must not relabel a late observation as an exact checkpoint.
- Public API gaps must remain absent/null with warnings rather than fabricated zeroes.

---

### Task 1: Planner-owned context merge

**Files:** modify `youtube-workflow/pipeline.py`, planner workflows, and tests; add generated projection/index JSON.

**Interfaces:** `load_planner_analytics(path) -> dict | None`; `build_context()` embeds that value as `analytics_summary` and otherwise builds normally.

- [ ] Add failing tests for missing, valid, invalid, stale, compact, and raw-data rejection cases.
- [ ] Implement the single optional merge path and remove duplicated workflow merge scripts.
- [ ] Repair stale pre-existing test fixtures without changing production behavior.
- [ ] Run planner unit/self-tests and commit.

### Task 2: Reporting and retention collectors

**Files:** create `runtime/analytics_reporting.py`, `runtime/analytics_retention.py`, tests.

**Interfaces:** reporting collection consumes a token, manifests, and file map; retention collection consumes eligible videos, completed checkpoints, and a query callable.

- [ ] Add failing tests for discovery/job deduplication, optional errors, downloaded-report deduplication, and due checkpoint selection.
- [ ] Implement dynamic channel/playlist report discovery, job persistence, CSV+metadata download, and 72h/7d retention capture.
- [ ] Run focused tests and commit.

### Task 3: Analytics v3 collection and derivation

**Files:** modify `runtime/analytics.py`; add analytics tests.

**Interfaces:** `run(planner_root, warehouse_root, collected_at) -> AnalyticsOutputs`; outputs include warehouse files and three planner contracts plus context.

- [ ] Add failing tests for 2h checkpoints, Shorts-source diagnostics, optional capability matrix, detailed summary, compact projection, and index.
- [ ] Expand supported Data/Analytics collection and accurate derived diagnostics.
- [ ] Keep the planner projection size-bounded and the detailed summary aggregated.
- [ ] Run focused tests and commit.

### Task 4: Dual-repository optimistic transport

**Files:** modify `runtime/analytics_remote.py`, `.github/workflows/analytics.yml`; add transport tests.

**Interfaces:** repository transport downloads exact main trees and commits only allow-listed passive/generated paths with SHA checks.

- [ ] Add failing tests for allowlists, stable retry identity, non-force updates, and credential redaction.
- [ ] Implement warehouse-first then planner commits with retry from current heads.
- [ ] Keep the workflow dispatch-only and reuse the existing token for both private repositories.
- [ ] Run runtime self-tests and commit.

### Task 5: Private warehouse and end-to-end verification

**Files:** create only `manifest/*.json` initially in `youtube-analytics-data`.

- [ ] Create the repository as private and verify it has no workflows or executable files.
- [ ] Push planner/runtime commits to current main using optimistic updates.
- [ ] Dispatch analytics, inspect job logs and resulting commits, and verify raw/derived/context separation.
- [ ] Run all acceptance checks, record SHAs/file sizes/API limitations, and report any required permission adjustment.
