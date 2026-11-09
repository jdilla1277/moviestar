// Browser-owned geometry for a resolved spatial frame. No user-authored HTML,
// script, remote URL, or stylesheet is loaded into the measurement page.
import {spawn} from 'node:child_process';
import {render} from './spatial_dom.mjs';
import {createInterface} from 'node:readline';
import {stopBrowser} from './spatial_browser_lifecycle.mjs';
import {mkdtempSync, readFileSync, writeFileSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const sessionMode = process.argv.includes('--session');
const requests = sessionMode ? createInterface({input: process.stdin, crlfDelay: Infinity}) : null;
// Attach the iterator before startup awaits, so early input is queued.
const requestIterator = requests?.[Symbol.asyncIterator]();
const request = sessionMode ? null : JSON.parse(readFileSync(0, 'utf8'));
const chromePath = process.argv[2];
const profile = process.argv[3] || mkdtempSync(join(tmpdir(), 'moviestar-layout-'));
const args = [
  '--headless=new', '--disable-gpu', '--hide-scrollbars',
  '--disable-background-networking', '--disable-extensions', '--no-first-run',
  '--force-device-scale-factor=1', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank',
];
if (process.getuid?.() === 0 || process.env.MOVIESTAR_CHROME_NO_SANDBOX === '1') {
  args.unshift('--no-sandbox');
}
const chrome = spawn(chromePath, args, {stdio: ['ignore', 'ignore', 'pipe']});
// A helper may inherit Chrome's stderr after the browser parent exits. The
// ChildProcess close event waits for that pipe and is not an exit signal.
const chromeExited = new Promise(resolve => chrome.once('exit', resolve));
let chromeError = '';
chrome.stderr.on('data', chunk => { chromeError = (chromeError + chunk.toString()).slice(-8192); });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
let nextId = 0;
const pending = new Map();

function startupError(reason) {
  const detail = chromeError.trim().split('\n').filter(Boolean).slice(-2).join(' | ');
  return new Error(detail ? `${reason}: ${detail}` : reason);
}

async function waitForPort() {
  // A cold CI browser launch can take longer than warm scene measurements.
  for (let attempt = 0; attempt < 600; attempt++) {
    const path = join(profile, 'DevToolsActivePort');
    if (existsSync(path)) {
      const port = Number(readFileSync(path, 'utf8').split('\n')[0]);
      if (Number.isInteger(port) && port > 0 && port <= 65535) return port;
    }
    if (chrome.exitCode !== null) throw startupError('Chrome exited before opening a debugging port');
    await pause(50);
  }
  throw startupError('Chrome did not open a debugging port within 30 seconds');
}

async function waitForTarget(port) {
  // Publishing DevToolsActivePort does not guarantee that discovery is already
  // accepting requests. Retry only this startup handshake, never a render.
  const deadline = Date.now() + 5000;
  let lastError = 'no page target yet';
  while (Date.now() < deadline) {
    if (chrome.exitCode !== null || chrome.signalCode !== null) {
      throw startupError('Chrome exited before opening a page target');
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`, {
        signal: AbortSignal.timeout(Math.max(1, Math.min(500, deadline - Date.now()))),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const pages = await response.json();
      if (!Array.isArray(pages)) throw new Error('invalid page target list');
      const target = pages.find(page => page?.type === 'page'
        && typeof page.webSocketDebuggerUrl === 'string' && page.webSocketDebuggerUrl);
      if (target) return target;
      lastError = 'no page target yet';
    } catch (error) {
      lastError = error.cause?.code ? `${error.message} (${error.cause.code})` : error.message;
    }
    const remaining = deadline - Date.now();
    if (remaining > 0) await pause(Math.min(50, remaining));
  }
  throw startupError(`Chrome opened no page target within 5 seconds (${lastError})`);
}

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
}

async function evaluate(expression) {
  const response = await send('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  });
  if (response.result?.exceptionDetails) {
    const detail = response.result.exceptionDetails;
    throw new Error((detail.exception?.description || detail.text || 'browser evaluation failed').split('\n')[0]);
  }
  return response.result.result.value;
}


async function renderRequest(request) {
  await send('Emulation.setDeviceMetricsOverride', {
    width: request.canvas.width, height: request.canvas.height,
    deviceScaleFactor: 1, mobile: false,
  });
  const measured = await evaluate(`(${render.toString()})(${JSON.stringify(request)})`);
  async function capture(path) {
    if (request.paint_dir) await evaluate('window.moviestarRepaint()');
    const screenshot = await send('Page.captureScreenshot', {
      format: 'png', captureBeyondViewport: false, fromSurface: true,
    });
    writeFileSync(path, Buffer.from(screenshot.result.data, 'base64'));
  }
  if (request.paint_dir) {
    await capture(join(request.paint_dir, 'base.png'));
    for (const plane of measured.video_planes) {
      await evaluate(`window.moviestarVideoView(${JSON.stringify(plane.id)}, 'white')`);
      await capture(join(request.paint_dir, `${plane.id}-white.png`));
      await evaluate("window.moviestarVideoView(null, 'black')");
    }
    if (request.reference_path) {
      await evaluate("window.moviestarVideoView(null, 'reference')");
      await capture(request.reference_path);
    }
    if (request.layout_output_path) {
      await evaluate('window.moviestarLabelView()');
      await capture(request.layout_output_path);
    }
  } else if (request.output_path) {
    await capture(request.output_path);
  }
  return measured;
}

async function withDeadline(operation) {
  // The Python caller enforces the complete request budget. This independent
  // bound also releases Chrome if that caller disappears during a CDP wait.
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error('browser operation timed out after 60 seconds')), 60000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

try {
  await withDeadline(async () => {
    if (Number(process.versions.node.split('.')[0]) < 22 || !globalThis.WebSocket) {
      throw new Error('Node.js 22 or newer is required');
    }
    const port = await waitForPort();
    const target = await waitForTarget(port);
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, {once: true});
      socket.addEventListener('error', reject, {once: true});
    });
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      const waiter = pending.get(message.id);
      if (!waiter) return;
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message);
    });
    const rejectPending = () => {
      for (const waiter of pending.values()) waiter.reject(new Error('Chrome debugging connection closed'));
      pending.clear();
    };
    socket.addEventListener('close', rejectPending);
    socket.addEventListener('error', rejectPending);
    await send('Page.enable');
    await send('Runtime.enable');
  });
  if (sessionMode) {
    for await (const line of requestIterator) {
      let envelope;
      try {
        envelope = JSON.parse(line);
        if (!Number.isInteger(envelope.id) || !envelope.request) throw new Error('invalid browser session request');
        const result = await withDeadline(() => renderRequest(envelope.request));
        process.stdout.write(JSON.stringify({id: envelope.id, result}) + '\n');
      } catch (error) {
        process.stdout.write(JSON.stringify({id: envelope?.id, error: error.message}) + '\n');
        process.exitCode = 1;
        break;
      }
    }
  } else {
    process.stdout.write(JSON.stringify(await withDeadline(() => renderRequest(request))));
  }
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
} finally {
  requests?.close();
  if (sessionMode) process.stdin.pause();
  try { socket?.close(); } catch {}
  const cleanupError = await stopBrowser(chrome, chromeExited, profile);
  if (cleanupError && !process.exitCode) {
    process.stderr.write(`Chrome profile cleanup failed: ${cleanupError.message}\n`);
    process.exitCode = 1;
  }
}
