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
    // Nodes default to a containing block; explicit static uses CSS ancestry.
    element.style.position = 'relative';
    element.style.minWidth = '0';
    element.style.minHeight = '0';
    if (node.kind === 'text' || node.kind === 'caption') {
      element.style.whiteSpace = 'pre-line';
      element.textContent = node.content;
    } else if (!input.paint_dir && (node.kind === 'video' || node.kind === 'image')) {
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

  // Measure the responsive layout before any visual transform. Motion never
  // changes flow sizing, text wrapping, or a sibling's layout position.
  const baseRects = new Map(entries.map(({element}) => [element, element.getBoundingClientRect()]));
  // Derive cumulative uniform scale from motion, not width ratios: a zero-width
  // caption can still paint glyphs, and 0/0 would disable overflow comparisons.
  const worldScales = new Map([[scene, 1]]);
  // Freeze resolved base opacity before modifying any ancestor. Inherited
  // opacity must not re-inherit an already animated multiplier and apply it
  // twice inside a subtree.
  const baseOpacity = new Map(entries.map(({element}) => [element, getComputedStyle(element).opacity]));
  for (const {element} of entries) element.style.opacity = baseOpacity.get(element);
  for (const {node, element} of entries) {
    worldScales.set(element, worldScales.get(element.parentElement)
      * (node.motion_transform ? node.motion.scale : 1));
    if (!node.motion) continue;
    const display = getComputedStyle(element).display;
    if (node.motion_transform && ['inline', 'contents', 'table-column', 'table-column-group'].includes(display)) {
      throw new Error(`${node.path.join('/')}.motion: display ${display} has no transformable box; use block or inline-block`);
    }
    // Transforming a static ancestor changes the containing block for absolute
    // descendants. Require the normal spatial default instead of silently
    // changing their base layout when motion is authored.
    if (node.motion_transform && getComputedStyle(element).position === 'static') {
      throw new Error(`${node.path.join('/')}.motion: animated targets require position relative or absolute; remove position static`);
    }
    const motion = node.motion;
    const opacity = Number(getComputedStyle(element).opacity) * motion.opacity;
    element.style.opacity = String(opacity);
    if (node.motion_transform) {
      // A fixed descendant originally uses the viewport as its containing
      // block. Establishing a transform would relocate it before motion even
      // begins, so surface that unsupported case instead of reporting false
      // base/visible geometry. Default relative/absolute subtrees stay stable.
      for (const descendant of entries) {
        if (descendant.element !== element && element.contains(descendant.element)
            && getComputedStyle(descendant.element).position === 'fixed') {
          throw new Error(`${descendant.node.path.join('/')}.motion: fixed descendants of transformed targets are unsupported; use absolute positioning`);
        }
      }
      element.style.transformOrigin = '0 0';
      element.style.transform = `translate(${motion.translate_x}, ${motion.translate_y}) scale(${motion.scale})`;
    }
  }

  const videoPlanes = [];
  const mediaPlanes = [];
  const mediaElements = [];
  if (input.paint_dir) {
    for (const {node, element} of entries) {
      if (!node.video && !node.image) continue;
      const computed = getComputedStyle(element);
      const padding = key => parseFloat(computed.getPropertyValue(`padding-${key}`)) || 0;
      const width = Math.max(0, element.clientWidth - padding('left') - padding('right'));
      const height = Math.max(0, element.clientHeight - padding('top') - padding('bottom'));
      if (node.video && !(width > 0 && height > 0)) {
        throw new Error(`${node.path.join('/')}: unresolved video geometry`);
      }
      const box = element.getBoundingClientRect();
      const worldScale = worldScales.get(element);
      const left = box.x + ((parseFloat(computed.borderLeftWidth) || 0) + padding('left')) * worldScale;
      const top = box.y + ((parseFloat(computed.borderTopWidth) || 0) + padding('top')) * worldScale;
      let nativeWidth, nativeHeight;
      if (node.image) {
        const image = new Image();
        image.src = node.image;
        await image.decode();
        nativeWidth = image.naturalWidth;
        nativeHeight = image.naturalHeight;
      } else {
        for (const source of [node.video.black_image, node.video.white_image, node.video.reference_image, node.video.raster_image].filter(Boolean)) {
          const preload = new Image();
          preload.src = source;
          await preload.decode();
        }
        nativeWidth = node.video.width;
        nativeHeight = node.video.height;
      }
      const fit = node.style['object-fit'] || 'fill';
      const contain = Math.min(width / nativeWidth, height / nativeHeight);
      const cover = Math.max(width / nativeWidth, height / nativeHeight);
      const factor = fit === 'contain' ? contain : fit === 'cover' ? cover
        : fit === 'none' ? 1 : fit === 'scale-down' ? Math.min(1, contain) : null;
      let fittedWidth = factor === null ? width : nativeWidth * factor;
      let fittedHeight = factor === null ? height : nativeHeight * factor;
      fittedWidth *= worldScale;
      fittedHeight *= worldScale;
      let x = left + (width * worldScale - fittedWidth) / 2;
      let y = top + (height * worldScale - fittedHeight) / 2;
      if (node.video) {
        x = Math.round(x); y = Math.round(y);
        fittedWidth = Math.round(fittedWidth); fittedHeight = Math.round(fittedHeight);
        if (!(fittedWidth > 0 && fittedHeight > 0)) {
          throw new Error(`${node.path.join('/')}: unresolved video geometry`);
        }
        const plane = {node, plane: element, rect: {x, y, width: fittedWidth, height: fittedHeight}};
        mediaPlanes.push(plane);
        if (!node.video.raster_image) videoPlanes.push(plane);
      }
      // Paint in the media element's content box. Background paint creates no
      // containing block or flow item, so authored static positioning, z-order,
      // ancestor clips, and normal-flow geometry remain intact.
      element.style.backgroundImage = `url(${JSON.stringify(node.image || node.video.raster_image || node.video.black_image)}), none`;
      // The final empty layer keeps the authored background color over the
      // padding/border box while only the media layer is clipped to content.
      element.style.backgroundOrigin = 'content-box, border-box';
      element.style.backgroundClip = 'content-box, border-box';
      element.style.backgroundRepeat = 'no-repeat';
      element.style.backgroundSize = `${fittedWidth / worldScale}px ${fittedHeight / worldScale}px`;
      element.style.backgroundPosition = `${(x - left) / worldScale}px ${(y - top) / worldScale}px`;
      mediaElements.push(element);
    }
  }

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
      base_rect: Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, rounded(baseRects.get(element)[key])])),
      layout_rule: node.style,
      rule_sources: node.rule_sources, matched_format_rules: node.matched_format_rules,
    };
    if (node.motion) box.motion = node.motion;
    let opacity = 1;
    for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
      opacity *= Number(getComputedStyle(ancestor).opacity);
    }
    box.effective_opacity = rounded(opacity);
    if (node.kind === 'text' || node.kind === 'caption') {
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
      if (node.kind === 'caption') {
        const css = getComputedStyle(element);
        const worldScale = worldScales.get(element);
        const px = key => (parseFloat(css.getPropertyValue(key)) || 0) * worldScale;
        const left = rect.left + px('border-left-width') + px('padding-left');
        const top = rect.top + px('border-top-width') + px('padding-top');
        const right = rect.right - px('border-right-width') - px('padding-right');
        const bottom = rect.bottom - px('border-bottom-width') - px('padding-bottom');
        box.caption = node.caption;
        box.overflow = lines.some(line => line.x < left - 1 || line.y < top - 1
          || line.x + line.width > right + 1 || line.y + line.height > bottom + 1);
        if (box.overflow) {
          diagnostics.push({code: 'caption_overflow', severity: 'error', path: node.path,
                            track: node.caption.track, line_count: lines.length,
                            reason: 'caption text exceeds its content box; enlarge the region or reduce font size'});
        }
      }
    }
    boxes.push(box);
    if (rect.width <= 0 || rect.height <= 0) {
      diagnostics.push({code: 'unresolved_geometry', path: node.path,
                        reason: 'measured box has zero width or height'});
    }
  }
  // Outline and labels are added after measurement, so the layout view cannot
  // move any measured box. It uses the exact page that supplied the rectangles.
  function labelView() {
    for (const element of mediaElements) {
      element.style.backgroundImage = 'none';
      element.style.backgroundClip = 'border-box';
    }
    for (const {node, element} of entries) {
      element.style.outline = '1px solid #ffb000';
      if (node.kind === 'text' || node.kind === 'caption') element.style.color = 'transparent';
      if (node.kind === 'video' || node.kind === 'image') element.style.backgroundColor = '#a9bed0';
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
  window.moviestarLabelView = labelView;
  window.moviestarRepaint = async () => {
    // Partial repaint can reuse rounded-clip edge pixels from a prior capture.
    // Rebuilding paint keeps the black/white calibration and reference aligned.
    document.body.style.display = 'none';
    void document.body.offsetHeight;
    document.body.style.display = 'block';
    await new Promise(resolve => requestAnimationFrame(resolve));
  };
  window.moviestarVideoView = (id, mode) => {
    for (const entry of videoPlanes) {
      const image = mode === 'reference' ? entry.node.video.reference_image
        : mode === 'white' && entry.node.video.id === id ? entry.node.video.white_image : entry.node.video.black_image;
      entry.plane.style.backgroundImage = `url(${JSON.stringify(image)}), none`;
    }
  };
  if (input.output_path && !input.paint_dir) labelView();
  return {boxes, diagnostics, scene_layout: input.scene_layout,
    media_planes: mediaPlanes.map(entry => ({id: entry.node.video.id, path: entry.node.path, rect: entry.rect})),
    video_planes: videoPlanes.map(entry => ({
    id: entry.node.video.id, path: entry.node.path, rect: entry.rect,
  }))};
}

try {
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
  await send('Page.enable');
  await send('Runtime.enable');
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
