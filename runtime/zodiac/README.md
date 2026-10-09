# Zodiac production on shared main

Zodiac supports one current request, execution and result contract. The fixed version marker is 2; there are no older-version adapters. Earlier development requests, executions and results were reset in zodiac-workflow. Drafts, approved media and publication times remain unchanged.

- Private `zodiac-workflow/pipeline.py` and `lifecycle.py` own planning validation, repairs, slot allocation, immutable requests/executions, history and context.
- `content.py` validates rendered content; `contract.py` checks the current request/execution, source commits and content hashes, and supplies result storage helpers.
- `transport.py` and `core.py` handle exact intake, production and verified result writeback.
- `publish.py` owns Zodiac channel authentication, scheduled-private upload, processing/metadata/schedule verification and recovery. It reuses the unmodified `output.state.GitHubState` write code with a fixed Zodiac repository and Zodiac-only evidence paths.
- `production.py` always uses the private catalogues. `cards.py` renders the six-second complete list and verifies layout, streams and full decoding; `media.py` selects approved backgrounds and music from `data/backgrounds.json` and `data/audio.json`. Missing catalogues fail production. An explicit offline black preview remains available for renderer checks.

The private dispatcher invokes `.github/workflows/zodiac.yml` on `main` with an execution ID and source commit. Runtime code uses the revision selected for that Actions run, like Dramas. Executions have no runtime pin; results record the actual runtime revision. Incomplete retries can use fixes, while completed results remain immutable and are checked before any new work.

`data/publish-slots.json` is the planner's schedule. New requests combine repository reservations with up to 250 recent YouTube uploads and pin the authenticated channel ID. Slots are reserved in draft array order at least ten minutes ahead; reruns retain their reservations. New requests publish on their reserved slots; existing immutable artifact-only requests remain artifact-only. There is one current contract with these two explicit production modes, without older-version adapters or extra publication configuration.

Zodiac uses its own entry point and `ZODIAC_STATE_TOKEN`, `ZODIAC_CLIENT_ID`, `ZODIAC_CLIENT_SECRET` and `ZODIAC_REFRESH_TOKEN` in the `exec` environment. The private dispatcher requires `PUBLIC_PRODUCTION_TOKEN`. Verified MP4/QC artifacts request 14-day retention in production-runtime; upload intent/upload evidence and verified results are written back. The passing artifact is saved before upload; its embedded render result reports local QC, while the final private result reports YouTube scheduling/publishing success. Separate private result/context workflows own history and context updates. Both repositories use only `main`.

Before upload, `intent.json` is acknowledged in `content/executions/evidence/<content_id>/`. After YouTube returns a video ID, `upload.json` records that ID and the exact request/video. Recovery uses the stored ID or scans for the unique content marker. An unresolved intent blocks any second upload. Processing still pending is a failed verification window, not a completed result; rerunning verifies the same video without rendering again. Expired slots are not silently moved. Abandonment is blocked once any intent exists.

Dramas' workflow, shared core, credentials, contracts and state are unchanged. Zodiac uses the same pinned base container without dependency installs during production. The removed illustrated renderers, runtime planner, old uploader and compatibility tests are not supported paths.

Verification: run the private tests with `ZODIAC_RUNTIME_PATH` set to this repository's `runtime` directory, and `PYTHONPATH=runtime python -m unittest discover -s runtime/tests`. Media tests need Pillow, ffmpeg/ffprobe and DejaVu fonts; offline contract tests do not contact YouTube.
