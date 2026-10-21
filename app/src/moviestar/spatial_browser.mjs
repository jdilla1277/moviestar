// Browser-owned geometry for a resolved spatial frame. No user-authored HTML,
// script, remote URL, or stylesheet is loaded into the measurement page.
import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const request = JSON.parse(readFileSync(0, 'utf8'));
const chromePath = process.argv[2];
const profile = mkdtempSync(join(tmpdir(), 'moviestar-layout-'));
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
let chromeError = '';
chrome.stderr.on('data', chunk => { chromeError = (chromeError + chunk.toString()).slice(-8192); });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
let nextId = 0;
const pending = new Map();

async function waitForPort() {
  const startupError = reason => {
    const detail = chromeError.trim().split('\n').filter(Boolean).slice(-2).join(' | ');
    return new Error(detail ? `${reason}: ${detail}` : reason);
  };
  // A cold CI browser launch can take longer than warm scene measurements.
  for (let attempt = 0; attempt < 300; attempt++) {
    const path = join(profile, 'DevToolsActivePort');
    if (existsSync(path)) return Number(readFileSync(path, 'utf8').split('\n')[0]);
    if (chrome.exitCode !== null) throw startupError('Chrome exited before opening a debugging port');
    await pause(50);
  }
  throw startupError('Chrome did not open a debugging port within 15 seconds');
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

async function render(input) {
  const {width, height} = input.canvas;
  function applyStyles(element, styles, where) {
    for (const [property, value] of Object.entries(styles)) {
      // Invalid setProperty calls preserve any existing preset declaration.
      if (!CSS.supports(property, value)) {
        throw new Error(`${where}.${property}: invalid CSS value ${JSON.stringify(value)}`);
      }
      element.style.setProperty(property, value);
    }
  }
  document.documentElement.style.cssText = 'margin:0;width:100%;height:100%';
  document.body.style.cssText = 'margin:0;width:100%;height:100%';
  document.body.replaceChildren();
  const scene = document.createElement('div');
  scene.style.cssText = 'box-sizing:border-box;position:relative;overflow:hidden';
  applyStyles(scene, input.root_style, 'scene.layout');
  // The requested canvas owns the outer size, regardless of scene rules.
  scene.style.width = `${width}px`;
  scene.style.height = `${height}px`;
  document.body.append(scene);
  const entries = [];

  function addNode(node, parent) {
    const element = document.createElement('div');
    element.style.boxSizing = 'border-box';
    // Every node is a containing block for its own positioned children.
    element.style.position = 'relative';
    element.style.minWidth = '0';
    element.style.minHeight = '0';
    if (node.kind === 'text') {
      element.style.whiteSpace = 'pre-line';
      element.textContent = node.content;
    } else if (node.kind === 'video' || node.kind === 'image') {
      element.style.backgroundColor = '#a9bed0';
    }
    applyStyles(element, node.style, node.path.join('/'));
    parent.append(element);
    entries.push({node, element});
    for (const child of node.children) addNode(child, element);
  }
  for (const node of input.nodes) addNode(node, scene);
  await document.fonts.ready;
  await new Promise(resolve => requestAnimationFrame(resolve));

  const boxes = [];
  const diagnostics = [];
  for (let index = 0; index < entries.length; index++) {
    const {node, element} = entries[index];
    const rect = element.getBoundingClientRect();
    const rounded = value => Math.round(value * 1000) / 1000;
    const box = {
      path: node.path, kind: node.kind, dom_order: index,
      rect: {x: rounded(rect.x), y: rounded(rect.y),
             width: rounded(rect.width), height: rounded(rect.height)},
      layout_rule: node.style,
    };
    if (node.kind === 'text') {
      const range = document.createRange();
      range.selectNodeContents(element);
      const lines = [];
      for (const fragment of range.getClientRects()) {
        if (fragment.width <= 0) continue; // newline caret, not a glyph line
        const line = lines.find(item => Math.abs(item.y - fragment.y) < 0.5);
        if (line) {
          const right = Math.max(line.x + line.width, fragment.right);
          line.x = Math.min(line.x, fragment.x);
          line.width = right - line.x;
          line.height = Math.max(line.height, fragment.height);
        } else {
          lines.push({x: fragment.x, y: fragment.y,
                      width: fragment.width, height: fragment.height});
        }
      }
      box.text_lines = lines.map(line => ({x: rounded(line.x), y: rounded(line.y),
                                           width: rounded(line.width), height: rounded(line.height)}));
    }
    boxes.push(box);
    if (rect.width <= 0 || rect.height <= 0) {
      diagnostics.push({code: 'unresolved_geometry', path: node.path,
                        reason: 'measured box has zero width or height'});
    }
  }
  // Outline and labels are added after measurement, so the layout view cannot
  // move any measured box. It uses the exact page that supplied the rectangles.
  if (input.output_path) {
    for (const {node, element} of entries) {
      element.style.outline = '1px solid #ffb000';
      if (node.kind === 'text') element.style.color = 'transparent';
    }
    for (const box of boxes) {
      const label = document.createElement('div');
      label.textContent = `${box.kind}: ${box.path.join('/')}`;
      label.style.cssText = 'position:absolute;z-index:2147483647;background:#13263a;color:white;'
        + 'font:12px monospace;padding:2px 4px;max-width:95%;white-space:nowrap;'
        + 'overflow:hidden;text-overflow:ellipsis;pointer-events:none';
      if (box.kind === 'component') {
        label.style.left = `${Math.min(width, Math.max(0, box.rect.x + box.rect.width))}px`;
        label.style.top = `${Math.max(0, box.rect.y + box.rect.height - 18)}px`;
        label.style.transform = 'translateX(-100%)';
      } else {
        label.style.left = `${Math.max(0, box.rect.x)}px`;
        label.style.top = `${Math.max(0, box.rect.y)}px`;
      }
      document.body.append(label);
    }
  }
  return {boxes, diagnostics};
}

try {
  if (Number(process.versions.node.split('.')[0]) < 22 || !globalThis.WebSocket) {
    throw new Error('Node.js 22 or newer is required');
  }
  const port = await waitForPort();
  let target;
  for (let attempt = 0; attempt < 100; attempt++) {
    const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    target = pages.find(page => page.type === 'page');
    if (target) break;
    await pause(50);
  }
  if (!target) throw new Error('Chrome opened no page target');
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
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: request.canvas.width, height: request.canvas.height,
    deviceScaleFactor: 1, mobile: false,
  });
  const measured = await evaluate(`(${render.toString()})(${JSON.stringify(request)})`);
  if (request.output_path) {
    const screenshot = await send('Page.captureScreenshot', {
      format: 'png', captureBeyondViewport: false, fromSurface: true,
    });
    writeFileSync(request.output_path, Buffer.from(screenshot.result.data, 'base64'));
  }
  process.stdout.write(JSON.stringify(measured));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
} finally {
  try { socket?.close(); } catch {}
  chrome.kill('SIGTERM');
  await pause(100);
  rmSync(profile, {recursive: true, force: true});
}
