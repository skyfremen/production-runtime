import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const artifactsDir = path.resolve('experiments/youtube-playback/artifacts');
fs.mkdirSync(artifactsDir, { recursive: true });

const url = process.env.YOUTUBE_URL || 'https://www.youtube.com/watch?v=jNQXAC9IVRw';
const report = {
  status: 'FAIL',
  classification: 'NOT_STARTED',
  url,
  checked_at: new Date().toISOString(),
  title: '',
  reason: '',
  media: null,
};

let browser;
let page;

function validateUrl(value) {
  const parsed = new URL(value);
  const hosts = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'];
  if (parsed.protocol !== 'https:' || !hosts.includes(parsed.hostname)) {
    throw new Error('URL must be an HTTPS YouTube or youtu.be link');
  }
  if (!['/watch', '/shorts/', '/live/'].some((prefix) =>
    parsed.pathname === prefix || parsed.pathname.startsWith(prefix))) {
    throw new Error('Expected a YouTube watch, Shorts, or live URL');
  }
}

async function mediaSnapshot() {
  if (!page) return null;
  return page.evaluate(() => {
    const video = document.querySelector('video.html5-main-video');
    if (!video) return null;
    return {
      currentTime: video.currentTime,
      duration: Number.isFinite(video.duration) ? video.duration : null,
      paused: video.paused,
      readyState: video.readyState,
      networkState: video.networkState,
      ended: video.ended,
      error: video.error ? { code: video.error.code, message: video.error.message } : null,
      adShowing: !!document.querySelector('#movie_player.ad-showing'),
    };
  }).catch(() => null);
}

try {
  validateUrl(url);
  browser = await chromium.launch({
    headless: true,
    args: ['--autoplay-policy=no-user-gesture-required'],
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    locale: 'en-US',
  });
  page = await context.newPage();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  report.title = await page.title();

  const video = page.locator('video.html5-main-video');
  await video.waitFor({ state: 'attached', timeout: 35000 });

  // Do not mistake a pre-roll advertisement for the requested video.
  await page.waitForFunction(() => {
    const player = document.querySelector('#movie_player');
    return player && !player.classList.contains('ad-showing');
  }, null, { timeout: 75000 });

  const startTime = await video.evaluate(async (element) => {
    element.muted = true;
    // Cloud-browser media play() can remain pending indefinitely.
    await Promise.race([
      element.play(),
      new Promise((_, reject) => setTimeout(
        () => reject(new Error('video.play() did not start within 15 seconds')),
        15000
      )),
    ]);
    return element.currentTime;
  });

  await page.waitForFunction((start) => {
    const element = document.querySelector('video.html5-main-video');
    const adShowing = !!document.querySelector('#movie_player.ad-showing');
    return element &&
      !adShowing &&
      !element.paused &&
      !element.error &&
      element.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
      element.currentTime - start >= 2;
  }, startTime, { timeout: 40000 });

  report.status = 'PASS';
  report.classification = 'PLAYBACK_OK';
  report.reason = 'Requested video progressed by at least 2 seconds.';
  console.log('PASS: Main YouTube video playback advanced by 2 seconds.');
} catch (error) {
  report.reason = String(error?.message || error);
  const bodyText = page
    ? await page.locator('body').innerText({ timeout: 5000 }).catch(() => '')
    : '';
  const challenge = /not a bot|sign in to confirm|unusual traffic|captcha|verify (you are|that you're) human|age.restricted|confirm your age|before you continue/i;
  report.classification = challenge.test(bodyText)
    ? 'ENVIRONMENT_BLOCKED'
    : 'PLAYBACK_FAILED';
  console.error('FAIL [' + report.classification + ']: ' + report.reason);
} finally {
  if (page) {
    report.media = await mediaSnapshot();
    await page.screenshot({
      path: path.join(artifactsDir, 'screenshot.png'),
      fullPage: false,
      timeout: 10000,
    }).catch((error) => console.error('Screenshot unavailable: ' + error.message));
  }
  fs.writeFileSync(path.join(artifactsDir, 'report.json'), JSON.stringify(report, null, 2));
  if (process.env.GITHUB_STEP_SUMMARY) {
    fs.appendFileSync(
      process.env.GITHUB_STEP_SUMMARY,
      '### YouTube playback smoke test\n\n' +
      '- Status: **' + report.status + '**\n' +
      '- Classification: \'\'' + report.classification + '\'\'\n' +
      '- URL: ' + report.url + '\n' +
      '- Reason: ' + report.reason.replace(/\n/g, ' ') + '\n'
    );
  }
  if (browser) await browser.close().catch(() => {});
}

if (report.status !== 'PASS') process.exitCode = 1;
