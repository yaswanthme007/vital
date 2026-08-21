"""M1 development-only annotation tool: a tiny local HTTP server + a single
self-contained HTML/JS page for hand-drawing the six vital ground-truth
boxes onto a sample_XXXX.png dataset (real or external-video), reusing the
existing sample_XXXX.json shape.

NOT a product feature -- this is a temporary M1 dev utility. No new
dependency: only the Python stdlib (http.server) + vanilla HTML/CSS/JS
served from one string, no external CDN, works fully offline. Chosen over a
cv2-window tool because this repo's venv uses opencv-python-headless (no
GUI/highgui support -- confirmed: cv2.namedWindow() raises "function not
implemented" here).

Usage:
    python -m app.eval.tier2_annotate --dataset app/eval/tier2_data/external_monitor_video
    (opens a browser tab at http://127.0.0.1:8765 automatically)

Controls (in the browser page):
    - Click an image in the left sidebar (checkmark = already has a saved
      annotation) or use Prev/Next.
    - Pick a vital (buttons or keys 1-6: hr/spo2/nibp/etco2/temp/rr).
    - Click-drag on the image to draw that vital's box -- replaces any
      existing box for the same vital on the same image.
    - "Clear this vital" removes just the selected vital's box.
    - "Save" (or Ctrl+S) writes sample_XXXX.json into the dataset dir in the
      exact {"id", "rois", "conditions", "notes"} shape
      ANNOTATION_GUIDE.md/real_monitor/README.md document, so
      app.eval.harness.load_dataset() and tier2_benchmark.py read it with
      zero code changes.
    - Existing sample_XXXX.json files (if any) are loaded and pre-populated
      on open, so re-running the tool never discards prior work.
"""

import argparse
import glob
import json
import os
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VITALS = ["hr", "spo2", "nibp", "etco2", "temp", "rr"]
VITAL_LABELS = {"hr": "HR", "spo2": "SpO2", "nibp": "NIBP", "etco2": "EtCO2", "temp": "Temp", "rr": "RR"}
_SAMPLE_ID_RE = re.compile(r"^sample_\d{4}$")


def _list_sample_ids(dataset_dir: str):
    pngs = sorted(glob.glob(os.path.join(dataset_dir, "sample_*.png")))
    return [os.path.splitext(os.path.basename(p))[0] for p in pngs]


def _annotation_path(dataset_dir: str, sample_id: str) -> str:
    if not _SAMPLE_ID_RE.match(sample_id):
        raise ValueError(f"Invalid sample id {sample_id!r}")
    return os.path.join(dataset_dir, f"{sample_id}.json")


PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Tier-2 M1 annotation tool</title>
<style>
  body { margin: 0; font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #1b1e24; color: #e6e6e6; display: flex; height: 100vh; }
  #sidebar { width: 190px; overflow-y: auto; background: #14161a; border-right: 1px solid #333; flex-shrink: 0; }
  #sidebar div.item { padding: 6px 10px; cursor: pointer; font-size: 13px; border-bottom: 1px solid #23262c; }
  #sidebar div.item:hover { background: #23262c; }
  #sidebar div.item.active { background: #2a4; color: #fff; }
  #sidebar div.item .mark { color: #6f6; margin-right: 6px; }
  #main { flex: 1; display: flex; flex-direction: column; }
  #toolbar { padding: 8px 12px; background: #22252b; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; border-bottom: 1px solid #333; }
  button { background: #333844; color: #eee; border: 1px solid #555; border-radius: 4px; padding: 6px 12px; cursor: pointer; font-size: 13px; }
  button:hover { background: #444a58; }
  button.vital { min-width: 64px; }
  button.vital.selected { background: #3a7; border-color: #3a7; color: #fff; font-weight: bold; }
  #canvasWrap { flex: 1; overflow: auto; display: flex; align-items: flex-start; justify-content: center; padding: 12px; }
  canvas { border: 1px solid #444; cursor: crosshair; background: #000; }
  #status { padding: 6px 12px; font-size: 12px; color: #9c9; min-height: 16px; }
  #meta { padding: 4px 12px 10px 12px; display: flex; gap: 10px; font-size: 12px; }
  #meta input, #meta textarea { background: #2a2d33; color: #eee; border: 1px solid #555; border-radius: 3px; padding: 3px 6px; }
  .legend { font-size: 11px; color: #999; padding: 0 12px 8px 12px; }
  .swatch { display: inline-block; width: 10px; height: 10px; margin-right: 4px; vertical-align: middle; }
</style>
</head>
<body>
<div id="sidebar"></div>
<div id="main">
  <div id="toolbar">
    <button onclick="prevImage()">&larr; Prev</button>
    <button onclick="nextImage()">Next &rarr;</button>
    <span style="width:1px;background:#444;align-self:stretch;"></span>
    <span id="vitalButtons"></span>
    <span style="width:1px;background:#444;align-self:stretch;"></span>
    <button onclick="clearCurrentVital()">Clear this vital</button>
    <button onclick="saveAnnotation()" style="background:#2a7;">Save (Ctrl+S)</button>
  </div>
  <div class="legend" id="legend"></div>
  <div id="meta">
    Conditions (comma-sep): <input id="conditions" size="30" placeholder="normal">
    Notes: <input id="notes" size="40">
  </div>
  <div id="status"></div>
  <div id="canvasWrap"><canvas id="cv"></canvas></div>
</div>
<script>
const VITALS = ["hr","spo2","nibp","etco2","temp","rr"];
const VITAL_LABELS = {hr:"HR",spo2:"SpO2",nibp:"NIBP",etco2:"EtCO2",temp:"Temp",rr:"RR"};
const VITAL_COLORS = {hr:"#39ff14",spo2:"#ffd400",nibp:"#ff4757",etco2:"#00d4ff",temp:"#ff9500",rr:"#bf5af2"};
let sampleIds = [];
let annotatedSet = new Set();
let currentIdx = 0;
let currentVital = "hr";
let rois = {};
let img = new Image();
let scale = 1;
const MAX_W = 1500, MAX_H = 820;
let drag = null;

const canvas = document.getElementById('cv');
const ctx = canvas.getContext('2d');

function buildVitalButtons() {
  const el = document.getElementById('vitalButtons');
  el.innerHTML = "";
  VITALS.forEach((v, i) => {
    const b = document.createElement('button');
    b.className = 'vital' + (v === currentVital ? ' selected' : '');
    b.textContent = (i+1) + ":" + VITAL_LABELS[v];
    b.style.borderColor = VITAL_COLORS[v];
    b.onclick = () => { currentVital = v; buildVitalButtons(); };
    el.appendChild(b);
  });
  const legend = document.getElementById('legend');
  legend.innerHTML = VITALS.map(v => `<span class="swatch" style="background:${VITAL_COLORS[v]}"></span>${VITAL_LABELS[v]}`).join('&nbsp;&nbsp;');
}

function buildSidebar() {
  const el = document.getElementById('sidebar');
  el.innerHTML = "";
  sampleIds.forEach((id, i) => {
    const d = document.createElement('div');
    d.className = 'item' + (i === currentIdx ? ' active' : '');
    d.innerHTML = (annotatedSet.has(id) ? '<span class="mark">&#10003;</span>' : '<span class="mark" style="visibility:hidden">&#10003;</span>') + id;
    d.onclick = () => loadImage(i);
    el.appendChild(d);
  });
}

async function refreshList() {
  const r = await fetch('/images');
  const data = await r.json();
  sampleIds = data.ids;
  annotatedSet = new Set(data.annotated);
  buildSidebar();
}

async function loadImage(i) {
  if (sampleIds.length === 0) return;
  currentIdx = ((i % sampleIds.length) + sampleIds.length) % sampleIds.length;
  const id = sampleIds[currentIdx];
  const annoResp = await fetch('/annotation/' + id);
  const anno = await annoResp.json();
  rois = anno.rois || {};
  document.getElementById('conditions').value = (anno.conditions || []).join(', ');
  document.getElementById('notes').value = anno.notes || '';

  img = new Image();
  img.onload = () => {
    scale = Math.min(MAX_W / img.width, MAX_H / img.height, 1);
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    redraw();
  };
  img.src = '/image/' + id + '?t=' + Date.now();
  buildSidebar();
  setStatus('Loaded ' + id + (Object.keys(rois).length ? (' -- ' + Object.keys(rois).length + ' box(es) already saved') : ' -- no annotation yet'));
}

function redraw() {
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  for (const v of VITALS) {
    const b = rois[v];
    if (!b) continue;
    const [x, y, w, h] = b;
    ctx.strokeStyle = VITAL_COLORS[v];
    ctx.lineWidth = v === currentVital ? 3 : 2;
    ctx.strokeRect(x * scale, y * scale, w * scale, h * scale);
    ctx.fillStyle = VITAL_COLORS[v];
    ctx.font = "13px sans-serif";
    ctx.fillText(VITAL_LABELS[v], x * scale + 2, y * scale - 4 < 10 ? y * scale + 14 : y * scale - 4);
  }
  if (drag) {
    ctx.strokeStyle = VITAL_COLORS[currentVital];
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 2;
    ctx.strokeRect(drag.x0, drag.y0, drag.x1 - drag.x0, drag.y1 - drag.y0);
    ctx.setLineDash([]);
  }
}

canvas.addEventListener('mousedown', e => {
  const r = canvas.getBoundingClientRect();
  drag = { x0: e.clientX - r.left, y0: e.clientY - r.top, x1: e.clientX - r.left, y1: e.clientY - r.top };
});
canvas.addEventListener('mousemove', e => {
  if (!drag) return;
  const r = canvas.getBoundingClientRect();
  drag.x1 = e.clientX - r.left;
  drag.y1 = e.clientY - r.top;
  redraw();
});
window.addEventListener('mouseup', e => {
  if (!drag) return;
  const x0 = Math.min(drag.x0, drag.x1), x1 = Math.max(drag.x0, drag.x1);
  const y0 = Math.min(drag.y0, drag.y1), y1 = Math.max(drag.y0, drag.y1);
  drag = null;
  if (x1 - x0 < 3 || y1 - y0 < 3) { redraw(); return; }
  const ox = Math.round(x0 / scale), oy = Math.round(y0 / scale);
  const ow = Math.round((x1 - x0) / scale), oh = Math.round((y1 - y0) / scale);
  rois[currentVital] = [ox, oy, ow, oh];
  setStatus('Drew ' + VITAL_LABELS[currentVital] + ' box -- remember to Save.');
  redraw();
});

function clearCurrentVital() {
  delete rois[currentVital];
  setStatus('Cleared ' + VITAL_LABELS[currentVital] + ' -- remember to Save.');
  redraw();
}

async function saveAnnotation() {
  const id = sampleIds[currentIdx];
  const conditions = document.getElementById('conditions').value.split(',').map(s => s.trim()).filter(Boolean);
  const notes = document.getElementById('notes').value;
  const body = { id, rois, conditions: conditions.length ? conditions : ['normal'], notes };
  const r = await fetch('/annotation/' + id, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (r.ok) {
    annotatedSet.add(id);
    buildSidebar();
    setStatus('Saved ' + id + '.json (' + Object.keys(rois).length + ' box(es)).');
  } else {
    setStatus('SAVE FAILED: ' + (await r.text()));
  }
}

function nextImage() { loadImage(currentIdx + 1); }
function prevImage() { loadImage(currentIdx - 1); }
function setStatus(s) { document.getElementById('status').textContent = s; }

window.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.ctrlKey && e.key === 's') { e.preventDefault(); saveAnnotation(); return; }
  if (e.key >= '1' && e.key <= '6') { currentVital = VITALS[parseInt(e.key) - 1]; buildVitalButtons(); redraw(); }
  if (e.key === 'ArrowRight') nextImage();
  if (e.key === 'ArrowLeft') prevImage();
  if (e.key === 'c') clearCurrentVital();
});

buildVitalButtons();
refreshList().then(() => loadImage(0));
</script>
</body>
</html>
"""


def make_handler(dataset_dir: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep the console quiet; this is a dev tool

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/":
                body = PAGE_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/images":
                ids = _list_sample_ids(dataset_dir)
                annotated = [i for i in ids if os.path.isfile(_annotation_path(dataset_dir, i))]
                self._send_json({"ids": ids, "annotated": annotated})
                return

            if path.startswith("/image/"):
                sample_id = path[len("/image/") :]
                try:
                    png_path = os.path.join(dataset_dir, f"{sample_id}.png")
                    if not _SAMPLE_ID_RE.match(sample_id) or not os.path.isfile(png_path):
                        self.send_error(404, "image not found")
                        return
                    with open(png_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as e:  # noqa: BLE001
                    self.send_error(500, str(e))
                return

            if path.startswith("/annotation/"):
                sample_id = path[len("/annotation/") :]
                try:
                    ap = _annotation_path(dataset_dir, sample_id)
                except ValueError as e:
                    self.send_error(400, str(e))
                    return
                if os.path.isfile(ap):
                    with open(ap) as f:
                        self._send_json(json.load(f))
                else:
                    self._send_json({"id": sample_id, "rois": {}, "conditions": [], "notes": ""})
                return

            self.send_error(404)

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if not path.startswith("/annotation/"):
                self.send_error(404)
                return
            sample_id = path[len("/annotation/") :]
            try:
                ap = _annotation_path(dataset_dir, sample_id)
            except ValueError as e:
                self.send_error(400, str(e))
                return

            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                self.send_error(400, f"bad json: {e}")
                return

            rois = payload.get("rois", {})
            for vital, box in rois.items():
                if vital not in VITALS:
                    self.send_error(400, f"unknown vital {vital!r}")
                    return
                if not (isinstance(box, list) and len(box) == 4):
                    self.send_error(400, f"box for {vital} must be [x,y,w,h]")
                    return

            record = {
                "id": sample_id,
                "rois": rois,
                "conditions": payload.get("conditions") or ["normal"],
                "notes": payload.get("notes", ""),
            }
            with open(ap, "w") as f:
                json.dump(record, f, indent=2)
            self._send_json({"ok": True})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True, help="Dataset dir containing sample_*.png to annotate")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser tab")
    args = parser.parse_args()

    dataset_dir = os.path.abspath(args.dataset)
    ids = _list_sample_ids(dataset_dir)
    if not ids:
        raise SystemExit(f"No sample_*.png files found under {dataset_dir}")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(dataset_dir))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Tier-2 M1 annotation tool: {len(ids)} images in {dataset_dir}")
    print(f"Serving at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
