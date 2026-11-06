# Deleted Zodiac Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Safely replace one manually deleted Zodiac upload with the contrast-fixed render.

**Architecture:** Validate explicit immutable authorization and authenticated remote deletion, atomically archive/reset the exact old active records, then dispatch the unchanged exact-execution workflow. Add an archive validator to private result ingestion so this deliberate transition is audited, not rejected or silently ignored.

**Tech Stack:** Python standard library, existing Zodiac publisher/contract, GitHub Actions, YouTube authenticated API.

**Spec:** docs/superpowers/specs/2026-11-06-deleted-zodiac-design.md

## Global Constraints

- Preserve immutable drafts, requests and execution IDs.
- Preserve the original future slot; never auto-reschedule.
- No API failure, missing evidence, live upload or mismatched identity may clear upload fences.
- Archive exact original bytes before deleting only the three active records.
- One replacement authorization/archive per content ID; repeated recovery must not clear new records.

## Review Focus

- Live old video or matching replacement marker must block without file mutation.
- Wrong channel and API exceptions must block without file mutation.
- Changed/missing evidence or expired slots must block without file mutation.
- Replay must preserve newly created evidence and results exactly.
- Archive commit ingestion must reject unrelated deletions or modified archived bytes.

### Task 1: Authenticated archive preparation

Files: create runtime/zodiac/replacement.py and runtime/tests/test_zodiac_replacement.py.
Interface: prepare(root, request_path, youtube, source_sha, runtime_sha) returns archive path, validates before writing; main fetches current main, commits only validated archive/deletions, pushes without force, outputs source_sha.
- [ ] Write synthetic tests for each Review Focus safety boundary and exact archive/reset success.
- [ ] Run tests RED, implement minimal module, run tests GREEN.
- [ ] Run full runtime unittest discovery and existing self-tests; commit implementation and tests atomically.

### Task 2: Workflow integration and archive audit

Files: create runtime replacement workflow, private authorization-dispatch workflow, private replacement_audit.py and tests; update private result.yml deletion guard only.
Interfaces: authorization JSON {replacement_version:1,execution_id,old_video_id,confirmed_deleted:true,reason}; runtime workflow takes source_sha and replacement_path, archives then dispatches zodiac.yml with same execution ID and new source_sha.
- [ ] Add real temporary-git archive validation tests RED; implement strict four-file diff audit; run GREEN.
- [ ] Commit workflows and audit, then confirm both repository CI runs succeed.

### Task 3: Recover the selected episode

- [ ] Add exactly one immutable authorization for ex-3b990746edc615826c1fe3fa / uU3AeUux8zs.
- [ ] Observe deletion verification/archive, exact production, upload verification and result ingestion.
- [ ] Check new video ID, runtime revision, unchanged publication slot and unrelated result records.
