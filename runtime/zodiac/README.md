# Zodiac renderer — offline MP4 artifact lane (Step 8)

This *independent* runtime lane now contains:
- `entrypoint.py`: read-only strict Zodiac plan intake.
- `renderer.py`: procedural silent animated frames with readable complete results; encodes H.264 MP4 at 1080×1920, 30 fps.
- `artifacts.py`: checks real MP4 via ffprobe, complete decode, black-frame detection, and actual extracted encoded-frame screenshots/contrast; creates `videos/`, `previews/`, `manifest.json`, and `qc-report.json` **only after all requested videos pass**.

**This repo never uploads an MP4 anywhere.** Its sole CLI output is a local directory; a separate **private** `skyfremen/zodiac-workflow` GitHub Actions workflow uses `actions/upload-artifact` to deliver that directory to the user. No Google OAuth, channel ID, YouTube API, publishing schedule or Wacky credentials are required or used. Do not call the existing `runtime/core.py` or production workflows.

Requires Python 3.11+, Pillow, local `ffmpeg` and `ffprobe`, and DejaVuSans fonts (available on Ubuntu GitHub runners). Run from the repo root:

    PYTHONPATH=runtime python runtime/zodiac/artifacts.py --handoff /tmp/private-zodiac-handoff.json --output /tmp/private-zodiac-preview

The bridge revalidates the signed-by-content editorial envelope (a SHA256 digest is *not authentication*), rejects Wacky production credentials and malformed plans, performs dry-run text fitting, renders N MP4s, and writes outputs atomically. Artifacts are retained only in the private caller's GitHub Actions storage; no private planning data or videos are committed to this public repository.

### Media QA caveats

Automated checks verify text bounds, required data presence, timing, video decode, stream dimensions, frame rate, soundtrack absence and extracted-frame contrast. Actual phone viewing, perceived font readability, visual humor and **loop quality** still require human review (Step 10). The last 0.2 seconds visually blend to the opening to avoid a hard black cut. Long mappings that cannot physically fit in the layout **fail closed** instead of being shrunk to unreadable font sizes.

### Verification and review

The existing public `Verify V2` checks continue to run unchanged. Extra optional Pillow-based Zodiac media tests are run **in the private `zodiac-workflow` CI**, where they exercise a genuine 1080×1920, 30-fps H.264 encode and fail-closed negative cases using fictional input. This avoids producing or uploading private creative assets in the public runtime repository.

For reviewed Zodiac episodes, the private, manual **Zodiac MP4 Preview Artifact (No YouTube)** workflow validates the complete approved draft, pins this runtime at an immutable commit SHA, runs `artifacts.py`, and delivers passing MP4s in a **private 14-day GitHub Actions artifact**. No media is committed to either repository. The private Actions run ID and artifact name provide the download link.

A real synthetic-video artifact was successfully generated and uploaded in the private repo at https://github.com/skyfremen/zodiac-workflow/actions/runs/37721974139; it is a technical demo only. Human quality review and an approved actual episode remain separate steps.
