FROM python:3.11-slim

# System deps (for cryptg C extension)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Render injects PORT automatically; default 8080 for local runs
ENV PORT=8080

CMD ["python", "bot.py"]
