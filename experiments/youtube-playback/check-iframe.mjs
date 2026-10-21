import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { chromium } from 'playwright';

const artifacts = path.resolve('experiments/youtube-playback/artifacts');
fs.mkdirSync(artifacts, { recursive: true });

function videoId(raw) {
  const url = new URL(raw);
  if (url.protocol !== 'https:' || !['youtube.com','www.youtube.com','m.youtube.com','youtu.be'].includes(url.hostname)) {
    throw new Error('Expected an HTTPS youtube.com or youtu.be link');
  }
  const id = url.hostname === 'youtu.be' ? url.pathname.slice(1) :
    url.pathname.startsWith('/shorts/') ? url.pathname.split('/')[2] :
    url.pathname.startsWith('/live/') ? url.pathname.split('/')[2] :
    url.pathname === '/watch' ? url.searchParams.get('v') : null;
  if (!id || !/^[a-zA-Z0-9_-]{11}$/.test(id)) throw new Error('Invalid YouTube video ID');
  return id;
}

const rawUrl = process.env.YOUTUBE_URL || 'https://www.youtube.com/watch?v=M7lc1UVf-VE';
const result = { method: 'official-iframe-player-api', url:rawUrl, status:'FAIL', id:null, reason:null, telemetry:null };
let server, browser, page;

try {
  const id = videoId(rawUrl);
  result.id = id;
  const html = `<!DOCTYPE html><html><head><title>YouTube Playback QA</title></head><body>
  <div id="player"></div><pre id="output"></pre><script>
  window.testState = { phase:'loading-api', currentTime:0, state:-1, error:null, ready:false, playing:false };
  let player;
  function update() {
    if (!player || !window.testState.ready) return;
    try {
      const v = player.getCurrentTime();
      if (Number.isFinite(v)) window.testState.currentTime = v;
      window.testState.state = player.getPlayerState();
      if (window.testState.state === 1 && v >= 2) window.testState.playing = true;
      document.getElementById('output').textContent = JSON.stringify(window.testState);
    } catch(e) { window.testState.exception = e.message; }
  }
  window.onYouTubeIframeAPIReady = function(){
    window.testState.phase='player-created';
    player = new YT.Player('player', {
      width: '854', height: '480', videoId: ${JSON.stringify(id)},
      playerVars: { autoplay: 1, mute: 1, playsinline: 1, origin: location.origin },
      events: {
        onReady: function(e) {
          window.testState.ready=true;
          window.testState.phase='ready';
          e.target.mute();
          e.target.playVideo();
        },
        onError: function(e){ window.testState.error=e.data; window.testState.phase='error'; },
        onStateChange: function(e) {window.testState.state=e.data;window.testState.phase='state-'+e.data;},
        onAutoplayBlocked: function() {window.testState.autoplayBlocked=true;}
      }
    });
  };
  setInterval(update, 400);
  const script=document.createElement('script');
  script.src='https://www.youtube.com/iframe_api';
  document.head.appendChild(script);
  </script></body></html>`;

  server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type':'text/html; charset=utf-8' });
    res.end(html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port=server.address().port;
  browser = await chromium.launch({ headless:true, args:['--autoplay-policy=no-user-gesture-required'] });
  page=await browser.newPage({viewport:{width:1000,height:740}, locale:'en-US'});
  await page.goto('http://127.0.0.1:'+port+'/', {waitUntil:'domcontentloaded', timeout:20000});
  await page.waitForFunction(() => window.testState?.playing || window.testState?.error, null, {timeout:55000});
  result.telemetry=await page.evaluate(() => window.testState);
  if(result.telemetry.playing && result.telemetry.currentTime >= 2) {
    result.status='PASS';
    result.reason='Official YouTube iframe player reports >=2 s playing';
    console.log('PASS: YouTube iframe API confirmed video playing for 2 seconds');
  } else {
    result.reason='YouTube iframe player error: '+ result.telemetry.error;
    console.error(result.reason);
  }
} catch(err) {
  result.reason=String(err?.message || err);
  if(page) result.telemetry=await page.evaluate(() => window.testState).catch(()=>null);
  console.error('Iframe test failed:', result.reason);
} finally {
  if(page) await page.screenshot({path:path.join(artifacts,'iframe.png')}).catch(()=>{});
  fs.writeFileSync(path.join(artifacts,'iframe-report.json'),JSON.stringify(result,null,2));
  console.log('IFRAME_RESULT:',JSON.stringify(result));
  if(browser) await browser.close().catch(()=>{});
  if(server) await new Promise(resolve=>server.close(resolve));
}
if(result.status!=='PASS') process.exitCode=1;
