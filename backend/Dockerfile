FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- IMPORTANT: Copy scripts and application code ---
# Copy the scripts folder from the root (relative to build context)
COPY scripts/ ./scripts/
# Copy everything inside the backend folder to /app
COPY . .

EXPOSE 8000

# The startup command is now managed by docker-compose