FROM python:3.11-slim

# espeak-ng: phonemizer data files for Piper (piper-phonemize bundles the shared
#            library but reads data from the system package at runtime)
# ffmpeg: real-time PCM → MP3 / Opus transcoding
# curl: model download + healthcheck probe
RUN apt-get update && apt-get install -y \
    espeak-ng \
    espeak-ng-data \
    libespeak-ng1 \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# piper-tts bundles piper-phonemize (espeak-ng Python bindings) + onnxruntime
RUN pip install --no-cache-dir \
    piper-tts \
    fastapi \
    "uvicorn[standard]"

WORKDIR /app

# Download en_US-lessac-high model (~65 MB ONNX + tiny JSON config)
# lessac-high: 22050 Hz, clean professional voice, low RTF on CPU
RUN curl -L -o /app/en_US-lessac-high.onnx \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx" && \
    curl -L -o /app/en_US-lessac-high.onnx.json \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx.json"

COPY server.py /app/server.py
COPY static /app/static
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/entrypoint.sh"]
