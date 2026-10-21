# YouTube playback smoke test (isolated experiment)

This is a standalone diagnostic experiment. It does **not** modify or run the Drama/Zodiac production workflows and has no access to production secrets.

## Run

1. Open GitHub **Actions** > **YouTube Playback Smoke Test** > **Run workflow**.
2. Enter a public YouTube watch URL or Shorts URL (or leave the default public sample).
3. Open the job summary and the **Print diagnostics** step to read the status and JSON report.

The workflow also runs on pushes that change this experiment or its own workflow file. Ordinary production commits do not trigger this experiment. To keep this experiment compatible with the repository's shell-only workflows, the screenshot and JSON are retained only on the temporary runner; the JSON is printed in the job logs and the summary is shown in GitHub Actions.

## What counts as success

Chromium loads the YouTube player and the **main video's `currentTime` advances by at least two seconds** while not paused or reporting an HTML media error. Page load alone is not enough. This does not check audio, video quality, full-length playback, or the experience on a real user's browser.

YouTube may rate-limit or block automated GitHub-hosted browsers, or require sign-in, age verification, or CAPTCHA. The script records an `ENVIRONMENT_BLOCKED` classification for recognizable cases. Such a result does **not** prove the video is broken. A video that refuses to advance is reported as `PLAYBACK_FAILED`; check the screenshot and report before concluding the video is unavailable.

Use this for occasional quality checks, not automated repeated view generation. The video does not need to be owned by this repository.
