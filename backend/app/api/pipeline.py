import io

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.pipeline.read_frame import read_frame

router = APIRouter(prefix="/api")


@router.post("/pipeline/read-frame")
async def read_frame_endpoint(file: UploadFile = File(...)) -> dict:
    """Runs one uploaded camera frame through the real detect -> rectify ->
    colour-ROI -> OCR pipeline (app.pipeline.read_frame) — the same code path
    the eval harness and replay pipeline source use, just fed a live frame
    instead of a pre-rendered/replayed one.
    """
    contents = await file.read()
    try:
        img = np.array(Image.open(io.BytesIO(contents)).convert("RGB"))
    except UnidentifiedImageError:
        raise HTTPException(status_code=422, detail="Uploaded file is not a readable image")

    reading, confidence = read_frame(img)
    return {"reading": reading, "confidence": confidence}
