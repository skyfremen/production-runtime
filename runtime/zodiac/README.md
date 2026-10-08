# Zodiac list production on shared main

The current list flow uses a single implementation in this repository:
- `content.py`: the existing normal JSON draft contract, sign coverage, score bounds, readable lengths and duplicate checks.
- `lifecycle.py`: compact repairs (maximum five), immutable requests and execution records, independent publication slots, result provenance and derived context.
- `cards.py`: full-screen list text; H.264/AAC encoding, font fit, ffprobe and complete decode/QC.
- `media.py`: deterministic selection from the private `data/background.json` and `data/audio.json`, native vertical 1080p Pexels footage, and six-second cosine loops. Music gain is the approved audition gain. Both catalogues are required together; missing or invalid assets reject production. Older states without either catalogue keep the silent black preview.
- `production.py`: exact request/execution intake and one-video production.
- `publish.py`: future opt-in publishing with Zodiac-only credentials, pinned channel identity and a durable upload reservation.

The private `skyfremen/zodiac-workflow` dispatches `.github/workflows/zodiac.yml` on `main`. The planner succeeds once GitHub accepts the handoff. The independent production job and MP4/QC artifact belong to **production-runtime**. Inputs are fetched from the exact private source revision using `ZODIAC_STATE_TOKEN`; verified results and derived context are written back to zodiac-workflow. Both repositories maintain only `main`; each request records the exact shared code revision used for reproducibility.

The existing Wacky Dramas `single.yml`, `runtime/core.py`, transport, contracts, credentials, state and upload route are unchanged. The Zodiac workflow uses the `exec` environment with its Zodiac-specific `ZODIAC_STATE_TOKEN`, runs only here on main, and fixes its private source to skyfremen/zodiac-workflow. Never route Zodiac requests through Dramas entrypoints.

Dispatch requires `PUBLIC_PRODUCTION_TOKEN` in zodiac-workflow (Actions write on production-runtime). Runtime intake and result writes require `ZODIAC_STATE_TOKEN` in this repository's `exec` environment (Contents read/write on zodiac-workflow). Existing Dramas tokens are not replaced. Passing artifacts request 14-day retention, subject to repository limits; already completed executions skip without creating a new artifact.

## Publication
Default: `publication.enabled=false`, `channel_id=null`. The dispatched artifact workflow rejects enabled publication and does not receive OAuth credentials. YouTube uploading will be enabled in a separate future change. The dormant publishing module requires an actual Zodiac channel ID and Zodiac-only OAuth credentials; the Dramas channel ID is explicitly refused.

Before any future video insertion, publishing must commit an execution reservation to the private planner's `content/uploads/` using a scoped private-state token. A known uploaded video is verified again; an uncertain reserved upload blocks a second insertion and requires reconciliation. Only verified scheduled uploads become successful publication results. No actual publication is enabled by this implementation.

The older illustrated/list-envelope modules (`entrypoint.py`, `renderer.py`, `list_renderer.py`, `artifacts.py`) retain their existing backward-compatible offline behavior; they are not used by the current planner contract.

## Verification
Run `PYTHONPATH=runtime python -m unittest discover -s runtime/tests` and the existing three runtime contract/self-test commands. Optional media tests require the already-declared Pillow, ffmpeg/ffprobe and DejaVu fonts. New synthetic flow tests exercise finalization, repair, exact identities, real MP4/QC, result ingestion and context without contacting YouTube.
