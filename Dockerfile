FROM python:3.11-slim

# System deps: espeak-ng (phonemizer backend), ffmpeg (MP3 encoding), curl for healthcheck
RUN apt-get update && apt-get install -y \
    espeak-ng \
    espeak-ng-data \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps:
#   kokoro   – official Kokoro 82M v1.0 inference library (auto-downloads weights)
#   soundfile – WAV I/O
#   fastapi + uvicorn – HTTP streaming server
#   pydub – MP3 encoding (requires ffmpeg)
RUN pip install --no-cache-dir \
    kokoro \
    soundfile \
    fastapi \
    "uvicorn[standard]" \
    pydub

WORKDIR /app

COPY server.py /app/server.py
COPY static /app/static
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# HuggingFace model cache — mount a volume here to persist downloads
ENV HF_HOME=/app/hf_cache
RUN mkdir -p /app/hf_cache

EXPOSE 5000

ENTRYPOINT ["/entrypoint.sh"]
