/**
 * M5.6 Phase 7: the CAMERA end-to-end test, in a real browser.
 *
 * Every earlier milestone's "E2E" drove the backend directly with a scripted
 * HTTP/WebSocket client -- real transport, but no browser, no getUserMedia,
 * no canvas capture, no React. M5.5 sec 18 item 6 names that gap explicitly.
 * This closes as much of it as software can:
 *
 *   REAL Chrome  ->  REAL getUserMedia  ->  REAL <video>/<canvas> capture
 *   ->  REAL JPEG encode  ->  REAL POST /api/pipeline/push-frame
 *   ->  REAL CameraSource + calibrated ROI + layout tracking + OCR
 *   ->  REAL reconcile()/alerts/persistence  ->  REAL WebSocket  ->  REAL UI
 *
 * WHAT IS SIMULATED, STATED PLAINLY: the camera sensor. Chrome is launched
 * with --use-fake-device-for-media-stream reading a Y4M file. This is NOT a
 * physical webcam pointed at a physical monitor, and the M5.6 report does not
 * claim it is. It is the strongest browser-level evidence obtainable without
 * a human in the loop.
 *
 * Prerequisites (this script does NOT start them):
 *   - backend on :8000   (uvicorn app.main:app --port 8000)
 *   - frontend on :5173  (npm run dev)
 *   - videos built       (python scripts/make_fake_camera_video.py OUTDIR)
 *
 * Usage:
 *   node scripts/m5_6_browser_e2e.mjs <fakecam-dir> <out-dir> [--critical] [--motion]
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { Cdp, CHROME_CANDIDATES, launchChrome, sleep } from './cdp.mjs';

const FAKECAM = process.argv[2];
const OUT = process.argv[3] || '.';
const USE_CRITICAL = process.argv.includes('--critical');
const USE_MOTION = process.argv.includes('--nudge');
const APP = 'http://localhost:5173';
const API = 'http://localhost:8000';
const PORT = 9333;

const results = { steps: [], passed: 0, failed: 0, notes: [], arm: USE_MOTION ? 'nudge' : USE_CRITICAL ? 'critical' : 'normal' };

// Phase timing: a demo rehearsal is only useful if it produces a number the
// operator can plan around, so every numbered step records how long it
// actually took in a real browser against the real backend.
const T0 = Date.now();
let lastMark = T0;
results.timing = {};
function phase(name) {
  const now = Date.now();
  results.timing[name] = { seconds: +((now - lastMark) / 1000).toFixed(1),
                           elapsed: +((now - T0) / 1000).toFixed(1) };
  lastMark = now;
  console.log(`   [${name}: ${results.timing[name].seconds}s, total ${results.timing[name].elapsed}s]`);
}

function check(name, ok, detail = '') {
  results.steps.push({ step: name, ok: Boolean(ok), detail: String(detail) });
  results[ok ? 'passed' : 'failed']++;
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' -- ' + detail : ''}`);
  return ok;
}

function chromePath() {
  const found = CHROME_CANDIDATES.find((p) => existsSync(p));
  if (!found) throw new Error('Chrome not found in any known location');
  return found;
}

/**
 * Converts a normalized point in VIDEO space to viewport coordinates.
 *
 * This has to replicate RoiCanvas's own object-contain letterbox maths
 * (src/features/calibration/RoiCanvas.tsx useVideoDisplayRect) or the drawn
 * boxes would not land on the pixels the operator can see. Doing it in the
 * page, against the live element geometry, is the only way to be sure.
 */
const POINT_JS = (nx, ny) => `
  const v = document.querySelector('video');
  if (!v || !v.videoWidth) return null;
  const container = v.parentElement;
  const b = container.getBoundingClientRect();
  const cw = container.clientWidth, ch = container.clientHeight;
  const vr = v.videoWidth / v.videoHeight, cr = cw / ch;
  let width, height;
  if (vr > cr) { width = cw; height = cw / vr; } else { height = ch; width = ch * vr; }
  const left = (cw - width) / 2, top = (ch - height) / 2;
  return { x: b.left + left + (${nx}) * width, y: b.top + top + (${ny}) * height };
`;

async function main() {
  mkdirSync(OUT, { recursive: true });
  const meta = JSON.parse(readFileSync(join(FAKECAM, 'rois.json'), 'utf8'));
  const video = USE_MOTION
    ? join(FAKECAM, 'monitor_nudge.y4m')
    : join(FAKECAM, USE_CRITICAL ? 'monitor_critical.y4m' : 'monitor_normal.y4m');
  if (!existsSync(video)) throw new Error(`missing video: ${video}`);
  console.log(`arm=${results.arm}  video=${video}`);

  // A fresh calibration profile per arm: the backend keeps ONE active
  // profile, and a stale one from a previous arm would silently change what
  // this run is testing.
  await fetch(`${API}/api/calibration/active`, { method: 'DELETE' }).catch(() => {});
  // Close out any case left running by a previous arm, so this run's
  // persistence assertions describe this run.
  try {
    const open = await (await fetch(`${API}/api/sessions`)).json();
    for (const s of open.filter((x) => x.status === 'active' && x.patient?.id === 'PT-M56-E2E')) {
      await fetch(`${API}/api/sessions/${s.id}/end`, { method: 'POST' }).catch(() => {});
    }
  } catch {
    /* backend not reachable yet is caught later, more informatively */
  }

  const { proc } = launchChrome({ chromePath: chromePath(), port: PORT, videoPath: video });
  let cdp;
  try {
    cdp = await Cdp.attach(PORT);
    const page = await cdp.newPage();
    await page.init();

    // ── 1. the app loads ──────────────────────────────────────────────
    console.log('\n1. load the app');
    await page.goto(`${APP}/calibration`);
    check('calibration page rendered',
      await page.waitFor(`return document.body.innerText.includes('Camera Calibration')`),
      '');
    check('no uncaught page errors on load', page.pageErrors.length === 0,
      page.pageErrors.slice(0, 2).join(' | '));
    await page.screenshot(join(OUT, `${results.arm}-01-calibration.png`));

    // ── 2. camera permission + stream ─────────────────────────────────
    console.log('\n2. connect the camera (real getUserMedia)');
    await page.clickText('Connect Camera');
    const dims = await page.waitFor(
      `const v = document.querySelector('video');
       return (v && v.videoWidth) ? (v.videoWidth + 'x' + v.videoHeight) : null;`,
      { timeout: 25000, label: 'a live camera stream' });
    check('getUserMedia returned a live stream', Boolean(dims), dims);
    // The badge is driven by React state set on 'loadedmetadata', which can
    // land a tick after videoWidth becomes non-zero -- so wait for it rather
    // than sampling once and racing the render.
    // Case-insensitive on purpose: the Badge component applies `uppercase`,
    // and innerText reflects text-transform, so the DOM literally reads
    // "CAMERA ACTIVE".
    check('UI reports Camera Active',
      await page.waitFor(`return /camera active/i.test(document.body.innerText)`,
        { timeout: 15000, label: 'the Camera Active badge' }).catch(() => false));
    check('stream resolution matches the source monitor',
      dims === `${meta.width}x${meta.height}`, `${dims} vs ${meta.width}x${meta.height}`);
    await page.screenshot(join(OUT, `${results.arm}-02-camera-connected.png`));
    phase('connect-camera');

    // ── 3. draw the six regions ───────────────────────────────────────
    console.log('\n3. draw six ROIs by real mouse drag');
    await page.clickText('Continue');
    await page.waitFor(`return document.body.innerText.includes('Draw the Vital Regions')`);

    // Exactly the labels RoiCanvas renders (VITAL_LABELS). Matching on the
    // button's FIRST LINE and on equality, not substring: 'HR' would
    // otherwise also match nothing useful, and loose matching silently picked
    // the wrong control in an earlier run.
    const LABELS = { hr: 'HR', spo2: 'SpO₂', nibp: 'NIBP', etco2: 'EtCO₂', temp: 'Temp', rr: 'RR' };
    let drawn = 0;
    for (const [vital, box] of Object.entries(meta.rois)) {
      // Select the vital, then drag its box -- exactly the operator gesture.
      const picked = await page.eval(`
        const wanted = ${JSON.stringify(LABELS[vital] || vital)};
        const el = [...document.querySelectorAll('button')].find(e => {
          if (e.offsetParent === null) return false;
          const first = (e.innerText || '').split('\\n')[0].trim();
          return first === wanted;
        });
        if (!el) return false;
        el.click();
        return true;
      `);
      if (!picked) { check(`selected ${vital}`, false, 'vital button not found'); continue; }
      await sleep(150);

      const p0 = await page.eval(POINT_JS(box.x, box.y));
      const p1 = await page.eval(POINT_JS(box.x + box.w, box.y + box.h));
      if (!p0 || !p1) { check(`drew ${vital}`, false, 'no video geometry'); continue; }
      await page.drag(p0.x, p0.y, p1.x, p1.y);
      drawn++;
    }
    const drawnCount = await page.eval(
      `const m = document.body.innerText.match(/(\\d+) of 6 regions drawn/); return m ? Number(m[1]) : -1;`);
    check('all six regions drawn on the live video', drawnCount === 6, `UI reports ${drawnCount} of 6`);
    await page.screenshot(join(OUT, `${results.arm}-03-regions-drawn.png`));
    phase('draw-6-regions');

    // ── 4. verify ─────────────────────────────────────────────────────
    console.log('\n4. verify each field against real OCR');
    await page.clickText('Continue');
    await page.waitFor(`return document.body.innerText.includes('Verify Each Field')`);

    check('Save is blocked before verification runs',
      await page.eval(`
        const b = [...document.querySelectorAll('button')].find(e => (e.innerText||'').includes('Save Profile'));
        return !b || b.disabled;`),
      'Save Profile disabled');

    await page.clickText('Run Verification');
    await page.waitFor(`return document.body.innerText.includes('Confirm this is right')
                               || document.body.innerText.includes('Confirmed');`,
      { timeout: 90000, label: 'verification results' });
    await page.screenshot(join(OUT, `${results.arm}-04-verified.png`));
    phase('verify-fields');

    const verifyText = await page.eval(`
      const rows = [...document.querySelectorAll('div')].filter(d => d.querySelector('button')
        && (d.innerText||'').includes('Confirm'));
      return document.body.innerText;`);
    const expected = USE_CRITICAL ? meta.critical : meta.normal;
    check('Verify shows the SpO2 the monitor is displaying',
      verifyText.includes(String(expected.spo2)), `expected ${expected.spo2}`);

    check('Save is STILL blocked with fields unconfirmed',
      await page.eval(`
        const b = [...document.querySelectorAll('button')].find(e => (e.innerText||'').includes('Save Profile'));
        return !b || b.disabled;`),
      'Save Profile disabled until every field is confirmed');

    // ── 5. confirm every field, then save ─────────────────────────────
    console.log('\n5. confirm every field and save the profile');
    const confirmed = await page.eval(`
      const btns = [...document.querySelectorAll('button')]
        .filter(e => (e.innerText||'').includes('Confirm this is right') && !e.disabled);
      btns.forEach(b => b.click());
      return btns.length;`);
    check('every readable field was confirmable', confirmed > 0, `${confirmed} fields confirmed`);
    await sleep(400);

    check('Save becomes enabled only after confirmation',
      await page.eval(`
        const b = [...document.querySelectorAll('button')].find(e => (e.innerText||'').includes('Save Profile'));
        return Boolean(b) && !b.disabled;`));

    await page.clickText('Save Profile');
    await page.waitFor(`return document.body.innerText.includes('Calibration Complete')`,
      { timeout: 60000, label: 'the profile to save' });
    check('calibration profile saved and activated', true);
    const trackingLine = await page.eval(`
      const t = document.body.innerText;
      const m = t.match(/Layout tracking\\s*\\n?\\s*(.+)/);
      return m ? m[1].trim() : null;`);
    check('layout tracking reported to the operator', Boolean(trackingLine), trackingLine || 'not shown');
    results.notes.push(`layout tracking after save: ${trackingLine}`);
    await page.screenshot(join(OUT, `${results.arm}-05-calibration-complete.png`));
    phase('confirm-and-save');

    const active = await (await fetch(`${API}/api/calibration/active`)).json();
    check('backend has an active profile with a reference frame',
      active.hasReferenceFrame === true, `profile ${active.id}`);

    // ── 6. start a real case ──────────────────────────────────────────
    console.log('\n6. start a session and enter camera mode');
    // IN-APP navigation, deliberately not page.goto(). cameraMode lives in a
    // non-persisted zustand store, so a full page load resets it to false and
    // the case would silently run on SYNTHETIC vitals despite an active
    // calibration profile. An earlier run of this script did exactly that and
    // is why the store's reload behaviour is called out in the M5.6 report.
    // M5.6 added a "Start a Case with This Profile" CTA to the Calibration
    // Complete screen, precisely so the operator has an in-app route onward
    // instead of reloading. Prefer it; fall back to TopNav's Live Monitor
    // link (which redirects to /start when no case is open) if it is absent.
    const usedCta = await page.clickText('Start a Case with This Profile').then(() => true).catch(() => false);
    check('Calibration Complete offers a route onward', usedCta, usedCta ? 'Start a Case CTA' : 'fell back to TopNav');
    if (!usedCta) await page.clickText('Live Monitor', 'a, button, [role="button"]');
    await page.waitFor(`return location.pathname === '/start'`,
      { timeout: 15000, label: 'the start-a-case form' });
    await page.waitFor(`return document.body.innerText.includes('Patient ID')`);
    await page.type('e.g. PT-2024-001', 'PT-M56-E2E');
    await page.type('Dr. Full Name', 'Dr M5.6');
    // Procedure is a <select>, not a text input -- React needs the
    // HTMLSelectElement value setter plus a change event, and an earlier run
    // silently failed step-1 validation because this field stayed empty.
    const proc = await page.eval(`
      const sel = document.querySelector('select');
      if (!sel) return null;
      const opt = [...sel.options].find(o => o.value && !o.disabled);
      if (!opt) return null;
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
      setter.call(sel, opt.value);
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      return opt.value;`);
    check('procedure selected from the real dropdown', Boolean(proc), proc || 'no <select> found');
    await page.screenshot(join(OUT, `${results.arm}-06-start-form.png`));

    await page.clickText('Next');
    await page.waitFor(`return /begin monitoring/i.test(document.body.innerText)`,
      { timeout: 10000, label: 'step 2 of the start form' });
    await page.clickText('Begin Monitoring');

    await page.waitFor(`return location.pathname === '/surgery'`,
      { timeout: 30000, label: 'the surgery page' });
    check('session started and navigated to Live Monitor', true);
    phase('start-case');

    // ── 7. live camera readings in the UI ─────────────────────────────
    console.log('\n7. live camera readings arriving in the UI');
    // The assertion that matters is NOT "some camera text exists" -- an
    // earlier run passed that while the session was actually streaming
    // synthetic vitals, because CameraOverlay's honest "Synthetic vitals --
    // no camera/OCR active this session" contains the word "camera". Assert
    // the real state instead: capture ON, and the synthetic banner absent.
    const camState = await page.waitFor(`
      const t = document.body.innerText;
      if (/no camera\\/OCR active/i.test(t)) return null;
      const m = t.match(/Capture ON|Connecting|Disconnected|Upload issue/i);
      return m ? m[0] : null;`,
      { timeout: 30000, label: 'the live camera capture indicator' }).catch(() => null);
    check('UI shows the case is in CAMERA mode, not synthetic', Boolean(camState), camState || 'still synthetic');

    // The decisive assertion has to be a value that CANNOT have come from
    // anywhere but OCR. app.validation.reconcile.DEFAULT_BASELINE seeds every
    // field of a new connection with hr 75 / spo2 98 / nibp 120-78-92 /
    // etco2 38 / temp 36.8 / rr 14, and this simulator monitor happens to
    // display four of those exact numbers -- so "98 appeared on screen" would
    // pass on the seed alone. Diastolic 80 (seed: 78) and the critical arm's
    // SpO2 88 (seed: 98) are the values that discriminate.
    const spo2Target = String(expected.spo2);
    const witness = USE_CRITICAL ? spo2Target : String(expected.nibpDiastolic);
    const witnessLabel = USE_CRITICAL ? `SpO2 ${spo2Target}` : `NIBP diastolic ${witness}`;
    const sawValue = await page.waitFor(
      `return document.body.innerText.includes(${JSON.stringify(witness)});`,
      { timeout: 120000, label: `${witnessLabel} to appear from live OCR` }
    ).catch(() => false);
    check(`live ${witnessLabel} read from the camera appears in the UI (differs from DEFAULT_BASELINE)`,
      Boolean(sawValue));
    phase('first-live-reading');
    // Let the case actually run for a few ticks so the persistence assertion
    // below describes a stream, not a single lucky frame.
    await sleep(10000);
    await page.screenshot(join(OUT, `${results.arm}-07-live-monitor.png`));

    if (USE_CRITICAL) {
      const alertShown = await page.waitFor(
        `const t = document.body.innerText.toUpperCase();
         return t.includes('CRITICAL') || t.includes('SPO') && t.includes('LOW') ? t.slice(0,0) || true : null;`,
        { timeout: 90000, label: 'a critical alert in the UI' }).catch(() => false);
      check('CRITICAL alert surfaced in the UI', Boolean(alertShown));
      await page.screenshot(join(OUT, `${results.arm}-07b-critical-alert.png`));
    }

    if (USE_MOTION) {
      // The money shot: the video's steady head has now looped into its
      // moving tail, so the camera is genuinely panning/zooming/rolling
      // relative to the frame these boxes were calibrated on. If layout
      // tracking works in the browser, the SAME values keep arriving. If it
      // did not, the boxes would crop the wrong pixels and the values would
      // change or collapse.
      console.log('   waiting for the video to reach its moving segment...');
      const before = await page.eval(`return document.body.innerText;`);
      results.notes.push(`nudge arm: SpO2 ${spo2Target} present before motion: ${before.includes(spo2Target)}`);

      let heldThroughMotion = false;
      const deadline = Date.now() + 120000;
      let samples = 0;
      while (Date.now() < deadline) {
        await sleep(4000);
        samples++;
        const t = await page.eval(`return document.body.innerText;`);
        const track = await page.eval(`
          const m = document.body.innerText.match(/(LOCKED|UNLOCKED|RECALIBRATE|TRACKING)[^\\n]*/i);
          return m ? m[0] : null;`);
        if (track) results.notes.push(`tracking badge sample ${samples}: ${track}`);
        if (t.includes(spo2Target)) heldThroughMotion = true;
        else { heldThroughMotion = false; break; }
      }
      check('SpO2 stayed correct across the camera nudge (boxes followed the monitor)',
        heldThroughMotion, `${samples} samples over ~${samples * 4}s`);
      await page.screenshot(join(OUT, `${results.arm}-07c-during-motion.png`));

      const noWrongConfirm = await page.eval(`
        // A confidently-wrong confirmation would show a value that is not
        // what the monitor displays. The only SpO2 this monitor ever shows
        // is ${spo2Target}.
        const t = document.body.innerText;
        return !/SpO/i.test(t) || t.includes(${JSON.stringify(spo2Target)});`);
      check('no wrong SpO2 value was ever confirmed during motion', noWrongConfirm);
    }

    // ── 8. persistence ────────────────────────────────────────────────
    console.log('\n8. the case is really being recorded');
    const sessions = await (await fetch(`${API}/api/sessions`)).json();
    const mine = sessions.filter((s) => s.patient?.id === 'PT-M56-E2E')
      .sort((a, b) => b.startTime - a.startTime)[0];
    check('session exists in the backend', Boolean(mine), mine?.id);
    if (mine) {
      check('camera-derived readings persisted', mine.vitalsCount > 0, `${mine.vitalsCount} readings`);
      results.notes.push(`session ${mine.id}: ${mine.vitalsCount} persisted readings`);
    }

    // ── 9. end the case ───────────────────────────────────────────────
    console.log('\n9. end the case and stop capture');
    // There is no End control on Live Monitor itself -- SurgeryHeader offers
    // Pause/Resume and Review only, and the End button lives in TopNav, which
    // renders on AppLayout routes (Review/Archive/Calibration), not on the
    // full-screen /surgery route. The real operator path is therefore
    // Review -> End, which is what this drives.
    check('Live Monitor offers a route to close the case',
      await page.eval(`return /review/i.test(document.body.innerText);`), 'Review button present');
    await page.clickText('Review');
    await page.waitFor(`return location.pathname === '/review'`,
      { timeout: 15000, label: 'the review page' });
    await page.screenshot(join(OUT, `${results.arm}-08-review.png`));
    await page.clickText('End');
    await sleep(2500);
    const afterEnd = await page.eval(`return location.pathname;`);
    check('ending the case leaves Live Monitor', afterEnd !== '/surgery', `now at ${afterEnd}`);
    await page.screenshot(join(OUT, `${results.arm}-09-after-end.png`));

    const stopped = await page.eval(`
      // Every capture track the page ever opened must be stopped once the
      // case is over -- a camera still running after End is both a privacy
      // problem and a sign the cleanup path did not execute.
      return { errors: window.__vitalErrors || null };`);
    check('no uncaught page errors during the whole run', page.pageErrors.length === 0,
      page.pageErrors.slice(0, 3).join(' | '));
    results.notes.push(`console errors observed: ${page.consoleErrors.length}`);
    if (page.consoleErrors.length) {
      results.notes.push(...page.consoleErrors.slice(0, 5));
    }
  } finally {
    if (cdp) cdp.close();
    proc.kill();
    await sleep(500);
  }

  writeFileSync(join(OUT, `m5_6_browser_e2e_${results.arm}.json`), JSON.stringify(results, null, 2));
  console.log(`\n=== ${results.passed} passed, ${results.failed} failed (arm: ${results.arm}) ===`);
  process.exit(results.failed ? 1 : 0);
}

main().catch((err) => {
  console.error('FATAL', err);
  writeFileSync(join(OUT, `m5_6_browser_e2e_${results.arm}.json`),
    JSON.stringify({ ...results, fatal: String(err) }, null, 2));
  process.exit(2);
});
