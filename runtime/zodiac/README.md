# Isolated Zodiac runtime lane — Step 7

This directory accepts the *private* `skyfremen/zodiac-workflow` planning handoff. It is deliberately separate from the Wacky Dramas V2 entrypoint (`runtime/core.py`), production workflow (`.github/workflows/single.yml`), output pipeline, YouTube upload code, tokens, state, channel identity and analytics.

**Currently validation-only.** The next Step 8 deliverable is a Zodiac-only renderer that writes MP4 files to **private GitHub Actions artifacts**, never YouTube. Rendering and artifact delivery are not yet implemented. This directory contains **no uploader, dispatcher, scheduler, OAuth, video renderer, credential fallback or external network request**. The presence of a valid handoff never means an MP4 has been produced.

## Contract

- A full, exact 40-character planning commit SHA; source repository pinned to `skyfremen/zodiac-workflow`.
- Contract `wacky-astrology-handoff-v1`, lane `zodiac`, visual spec 1080×1920/30fps, no narration.
- Exactly N editorially approved requests, N × 15 premises recorded as a pool count, with isolated `za-` concept IDs, scores and SHA-256 hashes.
- Re-check readable, complete sign/month assignment, required payoff and muted scenes. This is *structural validation*, not real mobile visual QA or proof of ownership/licensing.
- `render`, `upload`, `publish`, and `schedule` permissions must all be strictly `false`.
- Known Wacky production credentials in the process environment are rejected.
- The output is an isolated validation record with `ready_for_render=false`, `verified_video_files=[]`, and all execution flags disabled.
- A checksum is an integrity detection mechanism, **not cryptographic authentication or proof of a GitHub source commit**. The private workflow pins and checks the actual repository checkout separately.

## CLI

    PYTHONPATH=runtime python runtime/zodiac/entrypoint.py --input /tmp/zodiac-handoff.json --output /tmp/zodiac-validation.json

Use only the output of `zodiac_handoff.py` in the private planning repo. A malformed or altered handoff fails closed with exit 2 and no output file written.

The public `production-runtime` repository must **never store private Zodiac scripts, drafts, plans, tokens, results or identity secrets**. The private Zodiac workflow checks out this public repository at a pinned SHA and invokes the new offline lane within the private runner. Cross-repo tests use fictional fixtures without private content.

Upload authorization can only be added in a separate later task after Zodiac channel creation, independent OAuth, explicit target-channel-ID validation and private test approval.

## Step 8 target: downloadable video artifacts only

The next implementation should render **silent vertical 1080×1920, 30-fps MP4s** from the approved private Zodiac handoff; run ffprobe/ffmpeg and actual rendered-frame quality checks; then use the *private* `zodiac-workflow` GitHub Actions workflow to attach passing MP4(s), per-concept manifest and QC evidence using `actions/upload-artifact`. Present a run-summary download link. A 14-day artifact retention target is proposed, within repository limits. Never commit MP4 or private creative material to this public repository.

Treat this as the end of the pipeline for now. **No YouTube uploads, no channel creation, no OAuth, no publish slots and no schedule.** Do not modify the existing Wacky production workflow or call `runtime/core.py`, `runtime/output/execute.py`, the existing transport/transfer/upload helpers or any YouTube API from Zodiac. No Wacky credentials may be inherited. All YouTube steps remain deferred.

This paragraph is a specification for a future change, **not evidence that a renderer or video artifact already exists**.
