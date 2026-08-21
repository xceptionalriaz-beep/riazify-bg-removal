"""
Riazify — Background Removal Microservice
==========================================
FastAPI + rembg (BiRefNet model) — self-hosted, free, no per-image cost

Deploy on Railway:
  1. Push this folder to a GitHub repo
  2. Connect to Railway → New Project → Deploy from GitHub
  3. Add env var: BG_SERVICE_SECRET=your-random-secret
  4. Copy the Railway URL to your Next.js .env as BG_REMOVAL_SERVICE_URL

API:
  POST /remove-bg
    Header: x-api-secret: <BG_SERVICE_SECRET>
    Body: multipart/form-data — field "image" (file) OR field "image_url" (string)
    Returns: image/png (transparent background)

  GET /health
    Returns: {"status": "ok", "model": "birefnet-general"}
"""

import os
import io
import time
import httpx
import logging
from PIL import Image
from rembg import remove, new_session
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bg-removal")

# ── Config ─────────────────────────────────────────────────────
BG_SERVICE_SECRET = os.getenv("BG_SERVICE_SECRET", "dev-secret-change-me")
MODEL_NAME        = os.getenv("REMBG_MODEL", "u2net")
MAX_IMAGE_MB      = int(os.getenv("MAX_IMAGE_MB", "12"))
MAX_IMAGE_BYTES   = MAX_IMAGE_MB * 1024 * 1024

# ── App ────────────────────────────────────────────────────────
app = FastAPI(
    title="Riazify BG Removal",
    description="Self-hosted background removal using BiRefNet",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restricted by secret header
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# ── Load model once at startup ─────────────────────────────────
@app.on_event("startup")
async def load_model():
    global SESSION
    logger.info(f"Loading rembg model: {MODEL_NAME} ...")
    start = time.time()
    SESSION = new_session(MODEL_NAME)
    logger.info(f"Model loaded in {time.time() - start:.1f}s")

SESSION = None

# ── Auth dependency ────────────────────────────────────────────
def verify_secret(request: Request):
    secret = request.headers.get("x-api-secret", "")
    if secret != BG_SERVICE_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API secret")

# ── Health check ───────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "model_loaded": SESSION is not None,
    }

# ── Main endpoint ──────────────────────────────────────────────
@app.post("/remove-bg", dependencies=[Depends(verify_secret)])
async def remove_background(
    image: UploadFile | None = File(default=None),
    image_url: str | None    = Form(default=None),
    bg_color: str | None     = Form(default=None),  # e.g. "255,255,255" for white
):
    """
    Remove background from an image.

    - Upload a file directly (field: image), OR
    - Pass a URL (field: image_url)

    Optional: bg_color="255,255,255" to fill background with white instead of transparent.
    Returns: PNG image
    """
    if SESSION is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet, try again in a few seconds")

    # ── Get image bytes ───────────────────────────────────────
    if image is not None:
        raw = await image.read()
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail=f"Image too large. Max {MAX_IMAGE_MB}MB.")
    elif image_url:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                raw = resp.content
                if len(raw) > MAX_IMAGE_BYTES:
                    raise HTTPException(status_code=413, detail=f"Image too large. Max {MAX_IMAGE_MB}MB.")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch image URL: {e}")
    else:
        raise HTTPException(status_code=400, detail="Provide either 'image' file or 'image_url'")

    # ── Validate it's an image ────────────────────────────────
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        # Re-open after verify (verify() closes the file)
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file")

    # ── Remove background ─────────────────────────────────────
    start = time.time()
    try:
        result_bytes = remove(raw, session=SESSION)
    except Exception as e:
        logger.error(f"rembg error: {e}")
        raise HTTPException(status_code=500, detail="Background removal failed")
    elapsed = time.time() - start
    logger.info(f"Removed BG in {elapsed:.2f}s | size={len(raw)//1024}KB")

    # ── Optional: fill with solid colour ─────────────────────
    if bg_color:
        try:
            r, g, b = [int(x.strip()) for x in bg_color.split(",")]
            result_img = Image.open(io.BytesIO(result_bytes)).convert("RGBA")
            background = Image.new("RGBA", result_img.size, (r, g, b, 255))
            background.paste(result_img, mask=result_img.split()[3])
            out = io.BytesIO()
            background.convert("RGB").save(out, format="PNG")
            result_bytes = out.getvalue()
        except Exception:
            pass  # if fill fails, just return transparent

    return Response(
        content=result_bytes,
        media_type="image/png",
        headers={
            "X-Processing-Time": f"{elapsed:.2f}s",
            "Content-Disposition": "inline; filename=result.png",
        },
    )
