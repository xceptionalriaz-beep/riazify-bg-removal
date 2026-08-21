FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download BiRefNet-lite model at build time
# This avoids cold-start delay on first request
RUN python -c "from rembg import new_session; new_session('birefnet-lite'); print('BiRefNet-lite model ready')"

COPY main.py .

# Railway injects PORT dynamically
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
