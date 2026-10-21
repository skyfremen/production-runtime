# YouTube playback smoke test (isolated experiment)

This standalone experiment does not use or change the production pipelines or secrets. The workflow itself must remain in `.github/workflows/` for GitHub to discover it; everything else is in `experiments/youtube-playback/`.

## Run manually
Go to **Actions** → **YouTube Playback Smoke Test** → **Run workflow**, input a public video/Shorts URL (or accept default sample). Results and JSON diagnostics are printed in workflow logs; screenshots are saved only on the temporary runner.

## Tests
1. Official YouTube iframe JavaScript Player API with a localhost HTTP Referer: count as PASS only when reported play state is PLAYING and `getCurrentTime()` advances to at least 2 seconds. Logs API error codes.
2. If the official embedded player fails, use yt-dlp to download only the first ~5 seconds and FFmpeg to actually decode at least 15 video frames. Metadata alone is not a pass.

Neither test bypasses authentication, solves CAPTCHAs or guarantees playback for all viewers. GitHub runner IP restrictions may prevent both methods. Treat such failures as **inconclusive about the actual video**. This experiment is for occasional video QA, not repeated view generation.

Original standalone watch-page test remains in `check-playback.mjs` for diagnostics.

## Additional environments

A separate `youtube-playback-macos.yml` workflow tests the same official iframe on macOS.
A `youtube-playback-headed.yml` workflow tests full headed Chromium through Xvfb with the official privacy-enhanced YouTube embed domain. Both are isolated from production.

Previous attempts on hosted Linux and macOS returned YouTube player error 150 even for widely embedded demonstration videos; yt-dlp separately encountered YouTube's bot check. **Do not treat these runner failures as evidence that viewers cannot play the videos.** A self-hosted runner on a regular home/work network is the more appropriate next venue for browser QA if hosted runners remain blocked.

## Practical next options

- `youtube-availability.yml` is the fast, GitHub-hosted metadata availability check. A PASS means the official public oEmbed endpoint resolves the video; **it does not prove video playback**.
- `youtube-playback-self-hosted.yml` runs the actual 2-second `<video>` playback verification on a user's registered x64 Linux self-hosted runner with Python and Node.js. It is manual-only and remains queued until such a runner is configured. The GitHub Actions runner must have network access to YouTube.
- All previous hosted-browser experiments remain manual-only except the isolated headed experiment, whose push trigger is scoped to its own workflow file. Do not schedule repeated automated viewing.

A hosted GitHub runner may be blocked from YouTube media streaming; changing the browser does not guarantee that this restriction disappears. The availability check is intentionally not used as a substitute PASS for actual playback.
