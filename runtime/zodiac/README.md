# Zodiac production on shared main

Zodiac supports one current request, execution and result contract. The fixed version marker is 2; there are no older-version adapters. Earlier development requests, executions and results were reset in zodiac-workflow. Drafts, approved media and publication times remain unchanged.

- Private `zodiac-workflow/pipeline.py` and `lifecycle.py` own planning validation, repairs, slot allocation, immutable requests/executions, history and context.
- `content.py` validates rendered content; `contract.py` checks the current request/execution, source commits and content hashes, and supplies result storage helpers.
- `transport.py` and `core.py` handle exact intake, production and verified result-only writeback.
- `production.py` always uses the private catalogues. `cards.py` renders the six-second complete list and verifies layout, streams and full decoding; `media.py` selects approved backgrounds and music from `data/backgrounds.json` and `data/audio.json`. Missing catalogues fail production. An explicit offline black preview remains available for renderer checks.

The private dispatcher invokes `.github/workflows/zodiac.yml` on `main` with an execution ID and source commit. Runtime code uses the revision selected for that Actions run, like Dramas. Executions have no runtime pin; results record the actual runtime revision. Incomplete retries can use fixes, while completed results remain immutable and are checked before any new work.

`data/publish-slots.json` is the planner's schedule. Slots are reserved in draft array order at least ten minutes ahead; reruns retain their request reservations. `publication.enabled=false` and `channel_id=null` are fixed by planner code. There is no publication configuration file or active uploader. Publishing remains a separate future phase requiring Zodiac-only channel authentication and duplicate-upload protection.

Zodiac uses its own entry point and `ZODIAC_STATE_TOKEN` in the `exec` environment. The private dispatcher requires `PUBLIC_PRODUCTION_TOKEN`. Verified MP4/QC artifacts request 14-day retention in production-runtime; only results are written back. Separate private result/context workflows own history and context updates. Both repositories use only `main`.

`.github/workflows/zodiac-preview.yml` remains an explicit development catalogue preview using current planner main and the run's runtime revision. It does not reserve slots, write production records or upload to YouTube.

Dramas' workflow, shared core, credentials, contracts and state are unchanged. Zodiac uses the same pinned base container without dependency installs during production. The removed illustrated renderers, runtime planner, old uploader and compatibility tests are not supported paths.

Verification: run the private tests with `ZODIAC_RUNTIME_PATH` set to this repository's `runtime` directory, and `PYTHONPATH=runtime python -m unittest discover -s runtime/tests`. Media tests need Pillow, ffmpeg/ffprobe and DejaVu fonts; offline contract tests do not contact YouTube.
