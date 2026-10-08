# Zodiac list production on shared main

The current list flow uses a single implementation in this repository:
- `content.py`: the existing normal JSON draft contract, sign coverage, score bounds, readable lengths and duplicate checks.
- `lifecycle.py`: compact repairs (maximum five), immutable requests and execution records, independent publication slots, result provenance and derived context.
- `cards.py`: the migrated silent full-screen card renderer; actual H.264 encoding, font fit, ffprobe and complete decode/QC.
- `production.py`: exact request/execution intake and one-video production.
- `publish.py`: future opt-in publishing with Zodiac-only credentials, pinned channel identity and a durable upload reservation.

The private `skyfremen/zodiac-workflow` calls `.github/workflows/zodiac.yml@main` as a reusable workflow. Jobs, private creative inputs, MP4 artifacts and scoped result writes belong to the **private caller repository**, not this public repository. Both repositories maintain only `main`; each request records the exact shared code revision used for reproducibility.

The existing Wacky Dramas `single.yml`, `runtime/core.py`, transport, contracts, credentials, state and upload route are unchanged. The new workflow has its own `zodiac-exec` environment and refuses a different caller repository. Never route Zodiac requests through Dramas entrypoints.

## Publication
Default: `publication.enabled=false`, `channel_id=null`. Preview production never creates a YouTube client. Future opt-in requires an actual Zodiac channel ID and private environment secrets `ZODIAC_CLIENT_ID`, `ZODIAC_CLIENT_SECRET`, `ZODIAC_REFRESH_TOKEN`. The Dramas channel ID is explicitly refused.

Before any video insertion, publishing commits an execution reservation to the private planner's `content/uploads/` using the caller's scoped token. A known uploaded video is verified again; an uncertain reserved upload blocks a second insertion and requires reconciliation. Only verified scheduled uploads become successful publication results. No actual publication is enabled by this implementation.

The older illustrated/list-envelope modules (`entrypoint.py`, `renderer.py`, `list_renderer.py`, `artifacts.py`) retain their existing backward-compatible offline behavior; they are not used by the current planner contract.

## Verification
Run `PYTHONPATH=runtime python -m unittest discover -s runtime/tests` and the existing three runtime contract/self-test commands. Optional media tests require the already-declared Pillow, ffmpeg/ffprobe and DejaVu fonts. New synthetic flow tests exercise finalization, repair, exact identities, real MP4/QC, result ingestion and context without contacting YouTube.
