FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (Replaced libgl1-mesa-glx with libgl1)
RUN apt-get update && apt-get install -y \
    libgomp1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the BiRefNet model so it's baked into the image
RUN python -c "from rembg import new_session; new_session('u2net')"

COPY main.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
