FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    wget \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# ffmpeg: Whisper does not decode media itself — it shells out to the `ffmpeg`
# binary to extract audio. Without it, every transcription dies with
# FileNotFoundError: 'ffmpeg', which reads like a Python bug but is a missing
# system package. It works on macOS only because ffmpeg is on the host PATH.

# Step 1: Install standard packages from default PyPI
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    pyinstrument \
    gliner \
    sentence-transformers \
    passlib[bcrypt]==1.7.4 \
    bcrypt==4.0.1

# Step 2: Install PyTorch separately using the CPU-specific index
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Copy project structure
COPY . .

# Ensure PYTHONPATH is /app
ENV PYTHONPATH=/app

EXPOSE 8000