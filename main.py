"""
Riazify — AI Studio Background Removal Microservice
====================================================
FastAPI + BiRefNet-lite model + full post-processing pipeline

Pipeline per image:
  1. BiRefNet-lite removes background (2024 model, much better than u2net)
  2. Edge matting — feather edges to remove halos
  3. Pure white #FFFFFF fill
  4. Auto-crop to product bounds
  5. Center product with 5% padding on all sides
  6. Resize to minimum 1600×1600 (eBay recommendation)
  7. Subtle drop shadow for professional look
  8. Return optimised JPEG (eBay max 12MB)

GET  /health       — status + model info
GET  /warmup       — wake service before batch
POST /remove-bg    — process image (file upload or URL)
"""

import os, io, time, httpx, logging, math
from PIL import Image, ImageFilter, ImageDraw
from rembg import remove, new_session
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bg-removal")

# ── Config ──────────────────────────────────────────────────────
BG_SERVICE_SECRET = os.getenv("BG_SERVICE_SECRET", "dev-secret-change-me")
MODEL_NAME        = os.getenv("REMBG_MODEL", "birefnet-lite")
MAX_IMAGE_MB      = int(os.getenv("MAX_IMAGE_MB", "12"))
MAX_IMAGE_BYTES   = MAX_IMAGE_MB * 1024 * 1024
MIN_OUTPUT_PX     = int(os.getenv("MIN_OUTPUT_PX", "1600"))   # eBay min recommendation
PADDING_PCT       = float(os.getenv("PADDING_PCT", "0.05"))    # 5% padding around product
ADD_SHADOW        = os.getenv("ADD_SHADOW", "true").lower() == "true"

print(f"[STARTUP] Model: {MODEL_NAME} | Min output: {MIN_OUTPUT_PX}px | Shadow: {ADD_SHADOW}", flush=True)
print(f"[STARTUP] Secret prefix: {BG_SERVICE_SECRET[:6]}...", flush=True)

# ── App ─────────────────────────────────────────────────────────
app = FastAPI(
    title="Riazify AI Studio",
    description="Professional eBay product image pipeline",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

SESSION = None

# ── Load model at startup ────────────────────────────────────────
@app.on_event("startup")
async def load_model():
    global SESSION
    logger.info(f"Loading model: {MODEL_NAME} ...")
    t = time.time()
    SESSION = new_session(MODEL_NAME)
    logger.info(f"Model ready in {time.time()-t:.1f}s")

# ── Auth ────────────────────────────────────────────────────────
def verify_secret(request: Request):
    if request.headers.get("x-api-secret", "") != BG_SERVICE_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API secret")

# ── Post-processing pipeline ─────────────────────────────────────
def post_process(rgba: Image.Image, add_shadow: bool = True) -> bytes:
    """
    Full eBay-ready pipeline:
    1. Refine alpha edge (remove halos)
    2. Auto-crop to product
    3. Center with padding on white canvas
    4. Optional drop shadow
    5. Resize to MIN_OUTPUT_PX
    6. Return JPEG bytes
    """
    # ── 1. Edge refinement — remove halo/fringe pixels ──────────
    # Slightly erode the alpha mask then blur edges for clean anti-aliasing
    r, g, b, a = rgba.split()

    # Erode mask slightly to remove edge fringe
    a_clean = a.filter(ImageFilter.MinFilter(3))
    # Smooth the alpha edge
    a_clean = a_clean.filter(ImageFilter.GaussianBlur(radius=0.8))

    rgba.putalpha(a_clean)

    # ── 2. Auto-crop to product bounding box ────────────────────
    bbox = rgba.getbbox()
    if not bbox:
        # Blank image — return white square
        out = Image.new("RGB", (MIN_OUTPUT_PX, MIN_OUTPUT_PX), (255, 255, 255))
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=95)
        return buf.getvalue()

    product = rgba.crop(bbox)
    pw, ph = product.size

    # ── 3. Canvas — square with 5% padding ─────────────────────
    # Make canvas square based on longest side
    side = max(pw, ph)
    pad  = int(side * PADDING_PCT)
    canvas_size = side + pad * 2

    canvas = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 255))

    # Center product on canvas
    px = (canvas_size - pw) // 2
    py = (canvas_size - ph) // 2

    # ── 4. Drop shadow ──────────────────────────────────────────
    if add_shadow:
        # Create shadow from alpha mask
        shadow_offset_x = int(canvas_size * 0.012)  # 1.2% offset
        shadow_offset_y = int(canvas_size * 0.018)  # 1.8% offset (slightly more down)
        shadow_blur     = int(canvas_size * 0.025)  # 2.5% blur radius
        shadow_opacity  = 60                          # subtle, not heavy

        # Shadow layer
        shadow_layer = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        shadow_mask  = Image.new("L", product.size, 0)
        shadow_mask.paste(product.split()[3], (0, 0))

        # Fill shadow with semi-transparent black
        shadow_img = Image.new("RGBA", product.size, (0, 0, 0, shadow_opacity))
        shadow_layer.paste(shadow_img, (px + shadow_offset_x, py + shadow_offset_y), mask=shadow_mask)

        # Blur the shadow
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))

        # Composite: white → shadow → product
        canvas = Image.alpha_composite(canvas, shadow_layer)

    # Paste product on top
    canvas.paste(product, (px, py), mask=product.split()[3])

    # ── 5. Flatten to white RGB ─────────────────────────────────
    white = Image.new("RGB", canvas.size, (255, 255, 255))
    white.paste(canvas, mask=canvas.split()[3])

    # ── 6. Resize to minimum 1600px ────────────────────────────
    w, h = white.size
    if w < MIN_OUTPUT_PX or h < MIN_OUTPUT_PX:
        scale = MIN_OUTPUT_PX / min(w, h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        white = white.resize((new_w, new_h), Image.LANCZOS)

    # ── 7. Subtle unsharp mask to crisp product edges ──────────
    white = white.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))

    # ── 8. Export as JPEG ───────────────────────────────────────
    buf = io.BytesIO()
    white.save(buf, format="JPEG", quality=95, optimize=True)
    return buf.getvalue()


# ── Health ──────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status":       "ok",
        "model":        MODEL_NAME,
        "model_loaded": SESSION is not None,
        "pipeline":     ["birefnet-lite", "edge-refine", "autocrop", "center", "shadow", "1600px", "sharpen"],
        "version":      "2.0.0",
    }

@app.get("/warmup")
async def warmup():
    return {"ready": SESSION is not None, "model": MODEL_NAME}


# ── Main endpoint ────────────────────────────────────────────────
@app.post("/remove-bg", dependencies=[Depends(verify_secret)])
async def remove_background(
    image:      UploadFile | None = File(default=None),
    image_url:  str | None        = Form(default=None),
    bg_color:   str | None        = Form(default=None),  # legacy support
    shadow:     str | None        = Form(default=None),  # "false" to skip shadow
    pipeline:   str | None        = Form(default=None),  # "false" to skip post-process
):
    """
    Process a product image through the full AI Studio pipeline.
    Returns eBay-ready JPEG: white bg, centered, 1600px+, drop shadow.
    """
    if SESSION is None:
        raise HTTPException(status_code=503, detail="Model loading, retry in a few seconds")

    # ── Fetch image ──────────────────────────────────────────────
    if image is not None:
        raw = await image.read()
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail=f"Max {MAX_IMAGE_MB}MB")
    elif image_url:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(image_url)
                r.raise_for_status()
                raw = r.content
        except httpx.HTTPError as e:
            raise HTTPException(status_code=400, detail=f"Cannot fetch URL: {e}")
    else:
        raise HTTPException(status_code=400, detail="Provide 'image' file or 'image_url'")

    # ── Validate ────────────────────────────────────────────────
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image")

    t = time.time()

    # ── BiRefNet background removal ─────────────────────────────
    try:
        result_bytes = remove(raw, session=SESSION)
    except Exception as e:
        logger.error(f"rembg error: {e}")
        raise HTTPException(status_code=500, detail="Background removal failed")

    rgba = Image.open(io.BytesIO(result_bytes)).convert("RGBA")

    # ── Post-processing pipeline ────────────────────────────────
    run_pipeline = (pipeline or "true").lower() != "false"
    add_shadow   = ADD_SHADOW if shadow is None else shadow.lower() != "false"

    if run_pipeline:
        output_bytes = post_process(rgba, add_shadow=add_shadow)
        media_type   = "image/jpeg"
    else:
        # Legacy: just white fill, no pipeline
        if bg_color:
            try:
                rv, gv, bv = [int(x.strip()) for x in bg_color.split(",")]
            except Exception:
                rv, gv, bv = 255, 255, 255
        else:
            rv, gv, bv = 255, 255, 255
        bg = Image.new("RGBA", rgba.size, (rv, gv, bv, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        out = io.BytesIO()
        bg.convert("RGB").save(out, format="PNG")
        output_bytes = out.getvalue()
        media_type   = "image/png"

    elapsed = time.time() - t
    logger.info(f"Processed in {elapsed:.2f}s | in={len(raw)//1024}KB | out={len(output_bytes)//1024}KB | pipeline={run_pipeline}")

    return Response(
        content=output_bytes,
        media_type=media_type,
        headers={
            "X-Processing-Time": f"{elapsed:.2f}s",
            "X-Model":           MODEL_NAME,
            "X-Pipeline":        "full" if run_pipeline else "basic",
            "Content-Disposition": "inline; filename=product.jpg",
        },
    )
