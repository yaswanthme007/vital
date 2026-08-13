# python:3.12-slim, not the dev machine's 3.14 — 3.12 has the widest, most
# stable manylinux wheel coverage for numpy/opencv-python-headless/reportlab,
# and avoids the SQLAlchemy 2.0.36-vs-3.14 typing incompatibility hit during
# local dev (fixed by upgrading SQLAlchemy locally; 3.12 sidesteps it outright).
FROM python:3.12-slim

# System deps, installed once at build time (the only point this image needs
# network access):
#   - tesseract-ocr: the actual OCR binary. pytesseract (in requirements.txt)
#     is only a subprocess wrapper around it — without this package installed,
#     every OCR-pipeline code path raises TesseractNotFoundError at runtime.
#   - libgl1 / libglib2.0-0 / libgomp1: opencv-python-headless drops the GUI
#     bindings (no libGL/X11 dependency for *display*), but its compiled
#     extension module still dynamically links a few shared libs at import
#     time on slim Debian bases — most commonly libGL.so.1, libglib's
#     GObject runtime, and libgomp's OpenMP runtime. Omitting these is the
#     single most common "works in dev, ImportError in the container" trap
#     for this dependency; installing them here up front avoids it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# torch is pinned in requirements.txt for simulator/train/train_cnn.py (not
# imported by the running server, which does inference via onnxruntime) —
# pulling it from PyPI's default index resolves the CUDA build and its
# ~1GB of nvidia_* dependencies. Installing the CPU wheel first satisfies
# that pin (pip ignores a candidate's local version segment, e.g. "+cpu",
# when the requirements.txt specifier itself has none) without the bloat.
RUN pip install --no-cache-dir torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# FastAPI's UploadFile/File() (app/api/pipeline.py) needs python-multipart to
# parse the request — kept as its own layer rather than added to
# requirements.txt so it doesn't invalidate the (slow, network-flaky) layer
# above on every unrelated dependency tweak.
RUN pip install --no-cache-dir python-multipart==0.0.20

# Only what the running service needs: the app itself, the simulator package
# (ReplaySource's "pipeline" mode and the seed script both import it), and
# the seed/demo script. frontend-reference/, tests/, .venv/, and
# generated_reports/ are excluded via .dockerignore, not by omission here.
COPY app/ app/
COPY simulator/ simulator/
COPY scripts/ scripts/

# Overridden by docker-compose.yml to point at the persisted volume; these
# defaults just mean `docker run` alone (no compose) still works sensibly.
ENV DATABASE_URL=sqlite:////data/vital.db
ENV REPORTS_DIR=/data/generated_reports
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
