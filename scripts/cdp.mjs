/**
 * M5.6 Phase 7: a minimal Chrome DevTools Protocol client.
 *
 * Zero dependencies on purpose. Node 24 ships a global WebSocket, and CDP is
 * just JSON over one socket, so driving a REAL Chrome needs no test framework
 * and no browser download -- which matters for a milestone whose entire point
 * is freezing configuration rather than adding to it.
 *
 * Used by m5_6_browser_e2e.mjs. Nothing in the product imports this.
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
];

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Launch Chrome with a fake camera fed from a Y4M file.
 *
 * --use-fake-ui-for-media-stream auto-accepts the permission prompt (a human
 * clicking "Allow" is not something CDP can do -- the prompt is browser UI,
 * outside the page). --use-fake-device-for-media-stream + a file makes
 * getUserMedia return a real MediaStream carrying real monitor footage.
 * Everything downstream of the sensor is genuine.
 */
export function launchChrome({ chromePath, port, videoPath, userDataDir, headless = false }) {
  const dir = userDataDir || mkdtempSync(join(tmpdir(), 'vital-cdp-'));
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${dir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-features=Translate,MediaRouter',
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required',
    '--window-size=1440,960',
  ];
  if (videoPath) args.push(`--use-file-for-fake-video-capture=${videoPath}`);
  if (headless) args.push('--headless=new');
  const proc = spawn(chromePath, args, { stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stderr.on('data', () => {});
  proc.stdout.on('data', () => {});
  return { proc, userDataDir: dir };
}

async function fetchJson(url, tries = 60) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(url);
      if (res.ok) return await res.json();
    } catch {
      /* not up yet */
    }
    await sleep(500);
  }
  throw new Error(`Chrome DevTools endpoint never came up: ${url}`);
}

export class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id !== undefined && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message} (${JSON.stringify(msg.error.data ?? '')})`));
        else resolve(msg.result);
      } else if (msg.method) {
        for (const fn of this.listeners) fn(msg);
      }
    });
  }

  static async attach(port) {
    const version = await fetchJson(`http://127.0.0.1:${port}/json/version`);
    const ws = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', reject, { once: true });
    });
    return new Cdp(ws);
  }

  on(fn) {
    this.listeners.push(fn);
  }

  send(method, params = {}, sessionId) {
    const id = ++this.id;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    this.ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP timeout: ${method}`));
        }
      }, 60000);
    });
  }

  async newPage(url = 'about:blank') {
    const { targetId } = await this.send('Target.createTarget', { url });
    const { sessionId } = await this.send('Target.attachToTarget', { targetId, flatten: true });
    return new Page(this, sessionId, targetId);
  }

  close() {
    try {
      this.ws.close();
    } catch {
      /* already gone */
    }
  }
}

export class Page {
  constructor(cdp, sessionId, targetId) {
    this.cdp = cdp;
    this.sessionId = sessionId;
    this.targetId = targetId;
    this.consoleErrors = [];
    this.pageErrors = [];
  }

  send(method, params) {
    return this.cdp.send(method, params, this.sessionId);
  }

  async init() {
    await this.send('Page.enable');
    await this.send('Runtime.enable');
    await this.send('Log.enable');
    this.cdp.on((msg) => {
      if (msg.sessionId !== this.sessionId) return;
      if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params.exceptionDetails;
        this.pageErrors.push(d.exception?.description || d.text);
      } else if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error') {
        this.consoleErrors.push(msg.params.entry.text);
      }
    });
  }

  async goto(url) {
    await this.send('Page.navigate', { url });
    await sleep(1200);
  }

  /** Evaluate an expression in the page and return its value. */
  async eval(expression) {
    const res = await this.send('Runtime.evaluate', {
      expression: `(() => { ${expression} })()`,
      returnByValue: true,
      awaitPromise: true,
    });
    if (res.exceptionDetails) {
      throw new Error(
        `page eval failed: ${res.exceptionDetails.exception?.description || res.exceptionDetails.text}`
      );
    }
    return res.result.value;
  }

  /** Poll an expression until it returns something truthy. */
  async waitFor(expression, { timeout = 30000, interval = 250, label = expression } = {}) {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
      last = await this.eval(expression);
      if (last) return last;
      await sleep(interval);
    }
    throw new Error(`timed out waiting for: ${label}`);
  }

  /**
   * Click the first element whose visible text contains `text`.
   * React 17+ listens at the root container, and a native click dispatched
   * by el.click() bubbles there, so this drives real handlers.
   */
  async clickText(text, selector = 'button, a, [role="button"]') {
    const js = `
      const wanted = ${JSON.stringify(text)}.toLowerCase();
      const els = [...document.querySelectorAll(${JSON.stringify(selector)})];
      const el = els.find(e => (e.innerText || e.textContent || '').trim().toLowerCase().includes(wanted)
                              && !e.disabled && e.offsetParent !== null);
      if (!el) return false;
      el.click();
      return true;
    `;
    const ok = await this.eval(js);
    if (!ok) throw new Error(`no clickable element containing text: ${text}`);
    await sleep(250);
    return true;
  }

  /**
   * A real mouse drag through the input pipeline, not a synthetic React
   * event. RoiCanvas uses pointer events with setPointerCapture, which only
   * behave correctly for input Chrome itself generated.
   */
  async drag(x0, y0, x1, y1, steps = 12) {
    await this.send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: x0, y: y0, button: 'left', buttons: 1, clickCount: 1,
    });
    for (let i = 1; i <= steps; i++) {
      await this.send('Input.dispatchMouseEvent', {
        type: 'mouseMoved',
        x: x0 + ((x1 - x0) * i) / steps,
        y: y0 + ((y1 - y0) * i) / steps,
        button: 'left',
        buttons: 1,
      });
      await sleep(12);
    }
    await this.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: x1, y: y1, button: 'left', buttons: 0, clickCount: 1,
    });
    await sleep(150);
  }

  async type(selectorOrPlaceholder, value) {
    const js = `
      const key = ${JSON.stringify(selectorOrPlaceholder)};
      let el = null;
      // The key is allowed to be a placeholder fragment rather than a CSS
      // selector, and most placeholders ("e.g. PT-2024-001") are not valid
      // selectors -- querySelector throws on those rather than returning null.
      try { el = document.querySelector(key); } catch { el = null; }
      if (!el) el = [...document.querySelectorAll('input, textarea')]
        .find(e => (e.placeholder || '').toLowerCase().includes(key.toLowerCase()));
      if (!el) return false;
      const setter = Object.getOwnPropertyDescriptor(
        el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
        'value').set;
      setter.call(el, ${JSON.stringify(String(value))});
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    `;
    const ok = await this.eval(js);
    if (!ok) throw new Error(`no input matching: ${selectorOrPlaceholder}`);
    await sleep(120);
  }

  async screenshot(path) {
    const { data } = await this.send('Page.captureScreenshot', { format: 'png' });
    mkdirSync(join(path, '..'), { recursive: true });
    writeFileSync(path, Buffer.from(data, 'base64'));
    return path;
  }
}
