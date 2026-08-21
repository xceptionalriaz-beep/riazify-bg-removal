FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (Debian Trixie compatible)
RUN apt-get update && apt-get install -y \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the u2net model
RUN python -c "from rembg import new_session; new_session('u2net')"

COPY main.py .

# Railway injects PORT dynamically
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
