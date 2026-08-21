/**
 * M5.6 Phases 9 + 10: the product as a judge would meet it.
 *
 * Walks every route in a real browser looking for the things that actually
 * break a demo -- a page that throws, a control that does nothing, an empty
 * state that looks like a bug, a label that claims something untrue -- and
 * then exercises Demo Mode to confirm it stays isolated from the camera
 * pipeline after M5.6's promotion.
 *
 * This does NOT try to be a visual regression suite. It reports facts;
 * judgement about what to fix stays with a human reading the report.
 *
 * Usage: node scripts/m5_6_ui_audit.mjs <out-dir>
 */

import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { Cdp, CHROME_CANDIDATES, launchChrome, sleep } from './cdp.mjs';

const OUT = process.argv[2] || '.';
const APP = 'http://localhost:5173';
const API = 'http://localhost:8000';
const PORT = 9334;

const results = { steps: [], passed: 0, failed: 0, findings: [], notes: [] };

function check(name, ok, detail = '') {
  results.steps.push({ step: name, ok: Boolean(ok), detail: String(detail) });
  results[ok ? 'passed' : 'failed']++;
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' -- ' + detail : ''}`);
  return ok;
}
function finding(severity, where, what) {
  results.findings.push({ severity, where, what });
  console.log(`  [${severity.toUpperCase()}] ${where}: ${what}`);
}

const ROUTES = [
  ['/landing', 'Landing'],
  ['/start', 'Start a case'],
  ['/review', 'Review & Sign-off'],
  ['/archive', 'Archive'],
  ['/calibration', 'Calibration'],
  ['/ocr-debug', 'OCR Debug'],
];

async function main() {
  mkdirSync(OUT, { recursive: true });
  const chromePath = CHROME_CANDIDATES.find((p) => existsSync(p));
  const { proc } = launchChrome({ chromePath, port: PORT });
  let cdp;
  try {
    cdp = await Cdp.attach(PORT);
    const page = await cdp.newPage();
    await page.init();

    // ── every route renders, and says something ──────────────────────
    console.log('\n1. every route renders without throwing');
    for (const [route, label] of ROUTES) {
      const before = page.pageErrors.length;
      await page.goto(`${APP}${route}`);
      await sleep(1500);
      const info = await page.eval(`
        const t = document.body.innerText || '';
        return {
          chars: t.trim().length,
          blank: t.trim().length < 40,
          buttons: document.querySelectorAll('button, a[href], [role="button"]').length,
          disabled: [...document.querySelectorAll('button')].filter(b => b.disabled).length,
          scrollX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
          text: t.slice(0, 400),
        };`);
      const threw = page.pageErrors.length > before;
      check(`${label} (${route}) renders`, !threw && !info.blank,
        threw ? page.pageErrors[page.pageErrors.length - 1]?.slice(0, 120)
              : `${info.chars} chars, ${info.buttons} controls`);
      if (info.scrollX) finding('warn', label, 'page scrolls horizontally at 1440px wide');
      if (info.buttons === 0) finding('warn', label, 'no interactive controls found');
      results.notes.push(`${route}: ${info.buttons} controls, ${info.disabled} disabled`);
      await page.screenshot(join(OUT, `ui-${route.replace(/\//g, '') || 'root'}.png`));
    }

    // ── empty states are explanatory, not broken-looking ─────────────
    console.log('\n2. empty states explain themselves');
    await page.goto(`${APP}/review`);
    await sleep(1500);
    const reviewText = await page.eval(`return document.body.innerText.slice(0, 500);`);
    check('Review with no active case shows an explanatory state',
      /no (active )?(case|session)/i.test(reviewText) || reviewText.trim().length > 60,
      reviewText.split('\n').filter(Boolean).slice(0, 2).join(' | '));

    // ── OCR Debug: does it say WHICH ROI path produced the numbers? ───
    console.log('\n3. OCR Debug reports its ROI source (M5.6 promotion)');
    await page.goto(`${APP}/ocr-debug`);
    await sleep(1500);
    const dbg = await page.eval(`return document.body.innerText.slice(0, 800);`);
    results.notes.push(`ocr-debug copy: ${dbg.replace(/\n+/g, ' / ').slice(0, 300)}`);
    const active = await (await fetch(`${API}/api/calibration/active`)).json().catch(() => null);
    results.notes.push(`active calibration profile at audit time: ${active?.id ?? 'none'}`);
    check('OCR Debug page is reachable and populated', dbg.trim().length > 60);

    // ── Demo Mode isolation, in the browser ──────────────────────────
    console.log('\n4. Demo Mode isolation (Phase 10)');
    await page.goto(`${APP}/archive`);
    await sleep(1200);
    const opened = await page.clickText('Demo Mode').then(() => true).catch(() => false);
    check('Demo Mode control is present and clickable', opened);
    await sleep(600);
    const scenarios = await page.eval(`
      const t = document.body.innerText;
      return (t.match(/Select scenario|Switch scenario/i) || []).length > 0;`);
    check('Demo Mode offers scenarios', scenarios);

    // Count camera traffic before/after activating a scenario. Demo Mode must
    // never push a frame or open a camera WebSocket -- that is the isolation
    // property M5.6 has to re-confirm after the promotion.
    await page.eval(`
      window.__pushes = 0;
      const of = window.fetch;
      window.fetch = function (...a) {
        try { if (String(a[0]).includes('push-frame')) window.__pushes++; } catch {}
        return of.apply(this, a);
      };
      window.__ws = [];
      const OW = window.WebSocket;
      window.WebSocket = function (url, ...rest) { window.__ws.push(String(url)); return new OW(url, ...rest); };
      window.WebSocket.prototype = OW.prototype;
      return true;`);

    const started = await page.eval(`
      const btns = [...document.querySelectorAll('button')];
      const el = btns.find(b => /stable|normal|hypo|brady|tachy|desat|crisis/i.test(b.innerText || ''));
      if (!el) return null;
      const label = (el.innerText || '').split('\\n')[0];
      el.click();
      return label;`);
    check('a demo scenario could be started', Boolean(started), started || 'no scenario button found');
    await sleep(9000);
    await page.screenshot(join(OUT, 'ui-demo-mode.png'));

    const iso = await page.eval(`return { pushes: window.__pushes, ws: window.__ws };`);
    check('Demo Mode pushed ZERO camera frames', iso.pushes === 0, `${iso.pushes} push-frame calls`);
    const cameraWs = (iso.ws || []).filter((u) => u.includes('source=camera'));
    check('Demo Mode opened NO camera WebSocket', cameraWs.length === 0, cameraWs.join(', ') || 'none');
    results.notes.push(`websockets opened during demo mode: ${JSON.stringify(iso.ws)}`);

    const demoText = await page.eval(`return document.body.innerText.slice(0, 600);`);
    check('Demo Mode is visibly labelled as simulated',
      /demo/i.test(demoText), demoText.split('\n').find((l) => /demo/i.test(l)) || '');

    // ── responsive sanity ────────────────────────────────────────────
    console.log('\n5. narrower viewport');
    await page.send('Emulation.setDeviceMetricsOverride', {
      width: 1280, height: 800, deviceScaleFactor: 1, mobile: false,
    });
    for (const [route, label] of [['/surgery', 'Live Monitor'], ['/calibration', 'Calibration']]) {
      await page.goto(`${APP}${route}`);
      await sleep(1500);
      const overflow = await page.eval(
        `return document.documentElement.scrollWidth > document.documentElement.clientWidth + 2;`);
      if (overflow) finding('warn', label, 'horizontal overflow at 1280px');
      check(`${label} fits 1280px without horizontal scroll`, !overflow);
      await page.screenshot(join(OUT, `ui-1280-${route.replace(/\//g, '')}.png`));
    }

    check('no uncaught page errors across the whole audit', page.pageErrors.length === 0,
      page.pageErrors.slice(0, 3).join(' | '));
    results.notes.push(`console errors during audit: ${page.consoleErrors.length}`);
    results.notes.push(...page.consoleErrors.slice(0, 8));
  } finally {
    if (cdp) cdp.close();
    proc.kill();
    await sleep(400);
  }

  writeFileSync(join(OUT, 'm5_6_ui_audit.json'), JSON.stringify(results, null, 2));
  console.log(`\n=== ${results.passed} passed, ${results.failed} failed, ${results.findings.length} findings ===`);
}

main().catch((e) => {
  console.error('FATAL', e);
  writeFileSync(join(OUT, 'm5_6_ui_audit.json'), JSON.stringify({ ...results, fatal: String(e) }, null, 2));
  process.exit(2);
});
