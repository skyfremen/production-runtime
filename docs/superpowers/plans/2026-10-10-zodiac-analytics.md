# Zodiac Analytics Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans in this session. Steps are verified before commits.

**Goal:** Reuse Drama collection and safely expose Zodiac-native analytics to its planner.
**Architecture:** Default-preserving shared analytics profile, isolated Zodiac adapter/workflow/warehouse, planner-owned bounded context.
**Tech Stack:** Python standard library, unittest, YouTube Data/Analytics/Reporting APIs, GitHub Git API.
**Spec:** docs/superpowers/specs/2026-10-10-zodiac-analytics-design.md

## Global Constraints
Drama defaults and its repositories remain unchanged. Zodiac workflow is external workflow_dispatch only. Projection <=16000 bytes; context <=100000 bytes. Missing optional datasets never block normal planning. Three remote ref retries; exact existing write allowlists. No fixture channel metrics committed.

## Review Focus
Wrong channel or repository configuration must fail before collection. Future/abandoned/unverified results must be excluded. A large view count with weak response must not become an EVR recommendation. Missing reports stay missing; young samples never become mature patterns. Concurrent main updates must preserve planner changes.

### Task 1: Collection profile and Zodiac adapter
Files: runtime/analytics.py, runtime/analytics_remote.py, runtime/zodiac/analytics.py, runtime/tests/test_zodiac_analytics.py.
Interfaces: AnalyticsProfile config passed to run/snapshot/summary; Zodiac run(planner_root, warehouse_root, collected_at) returns existing AnalyticsOutputs.
- [ ] Write and run failing isolation, verified-result, mature-evidence and compact-size tests.
- [ ] Add default-preserving profile arguments; reuse collectors and remote writer with injected runner/repositories.
- [ ] Implement native Zodiac mapping, channel guard, format/headline summaries and response-oriented compact projection.
- [ ] Run all runtime tests and contract/self tests, then review shared defaults.

### Task 2: Planner integration and operation
Files: zodiac-workflow/lifecycle.py, tests/test_analytics.py, .github/workflows/context.yml, PLANNING.md; production-runtime/.github/workflows/zodiac-analytics.yml and runtime/zodiac/README.md.
Interfaces: load_planner_analytics(path) returns valid compact dict or None; build_context(root) embeds analytics_summary within bound.
- [ ] Write and run failing valid/invalid/oversized/missing/context-preservation tests.
- [ ] Add saved-signal embedding, context trigger and Zodiac analytics-aware selection instructions.
- [ ] Add isolated dispatch workflow and document warehouse/scopes/external schedule setup.
- [ ] Run full planner suite and workflow validation; publish runtime first then planner with expected-head checks.

### Task 3: Activation and verification
- [ ] Verify remote CI and diff scope.
- [ ] Create private passive warehouse and initialize manifests through authorized available access.
- [ ] Dispatch collection; inspect real OAuth, repository access, first snapshot, summary and context.
- [ ] Report any remaining external scheduling or credential setup with exact actions.
