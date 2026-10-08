# Astrology flow on shared main

Goal: Implement the approved twelve-step flow without changing Dramas source, contracts, workflows, credentials or state.

Architecture: The private zodiac-workflow repo owns immutable drafts, requests, execution records, failures, results, publication configuration and derived context. production-runtime/main owns the single implementation of Zodiac validation, lifecycle and card rendering, invoked through an isolated reusable workflow. Existing Dramas entrypoints remain byte-for-byte unchanged.

Constraints: main only; no new dependencies, analytics, experiments, actual episode generation or uploads during implementation. Normal planner JSON remains unchanged. Default publication is disabled. Private input and MP4 artifacts stay in the private caller's Actions run. No runtime/core.py or Wacky credentials in the Zodiac lane.

1. Shared lifecycle: move the existing private validator and renderer to zodiac/content.py and zodiac/cards.py, leaving thin compatibility wrappers. Add zodiac/lifecycle.py with immutable finalization, exact compact repairs (maximum five), count repairs, independent slots, deterministic request/execution IDs, result ingestion and bounded context. Test invalid winners, unaffected-winner preservation, repair ancestry, duplicate slots/IDs, reruns, path traversal and stale results.
2. Shared production: add zodiac/production.py and reusable zodiac.yml. Pin request/execution identity and runtime code, render/QC each execution, keep artifacts private, and record results. Add isolated future publishing with channel identity checks and durable upload reservation; preview mode never creates a YouTube client. Test wrong repository/channel, tampered requests, disabled publishing, duplicate-upload prevention and result provenance.
3. Private wiring and editorial process: produce.yml finalizes and directly calls the reusable workflow for each execution. Tests check shared code and existing private contract tests. Update PLANNING.md for context, max(30,10N), max(6,2N), 8+2 hook tournament, three answer approaches, fidelity audit and repair contract. Preserve complete simultaneous lists and existing quality/format constraints.

Verification: run existing runtime regression suite and contract/self-tests, private contract/media tests, workflow structural checks and a synthetic end-to-end finalization/render/QC/result/context test. Independent reviewer checks the diff before connector commits directly to both main branches. Commit runtime first, then private wiring. No real draft is committed or dispatched.

Review focus: preserve exact Dramas bytes; caller token scopes private result writes; concurrent finalization cannot reuse slots; failed/repaired drafts cannot contaminate duplicate history; a failed remote upload cannot be retried as a new upload.
