# Riazify BG Removal Service

Self-hosted background removal API using **rembg + BiRefNet** model.
Free, no per-image cost, production-ready.

## Deploy on Railway (Free)

1. Create a new GitHub repo and push this `bg-removal-service/` folder
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo → Railway auto-detects the Dockerfile
4. Add environment variables:
   ```
   BG_SERVICE_SECRET=your-random-secret-here
   REMBG_MODEL=birefnet-general
   MAX_IMAGE_MB=12
   ```
5. Deploy — Railway gives you a URL like `https://your-app.up.railway.app`
6. Add to your Next.js `.env`:
   ```
   BG_REMOVAL_SERVICE_URL=https://your-app.up.railway.app
   BG_SERVICE_SECRET=your-random-secret-here
   ```

## API

### Health Check
```
GET /health
```

### Remove Background
```
POST /remove-bg
Header: x-api-secret: your-secret
Body (multipart): image=<file>  OR  image_url=<url>
Optional: bg_color=255,255,255  (white background instead of transparent)
Returns: image/png
```

## Test locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run
uvicorn main:app --reload

# Test
curl -X POST http://localhost:8000/remove-bg \
  -H "x-api-secret: dev-secret-change-me" \
  -F "image=@/path/to/photo.jpg" \
  --output result.png
```

## Models

| Model | Quality | Speed | Size |
|-------|---------|-------|------|
| `birefnet-general` | ⭐⭐⭐⭐⭐ | ~3-5s CPU | 175MB |
| `u2net` | ⭐⭐⭐⭐ | ~2-3s CPU | 168MB |
| `isnet-general-use` | ⭐⭐⭐⭐ | ~3s CPU | 168MB |

Default is `birefnet-general` — best quality for product photos.
