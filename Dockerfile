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

# Download multiple Piper voice models
# Each voice includes: .onnx (model) + .onnx.json (config)

# US English - Lessac (Male, High Quality) - Default
RUN curl -L -o /app/en_US-lessac-high.onnx \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx" && \
    curl -L -o /app/en_US-lessac-high.onnx.json \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx.json"

# US English - Amy (Female, Medium)
RUN curl -L -o /app/en_US-amy-medium.onnx \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx" && \
    curl -L -o /app/en_US-amy-medium.onnx.json \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json"

# US English - Ryan (Male, High Quality)
RUN curl -L -o /app/en_US-ryan-high.onnx \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx" && \
    curl -L -o /app/en_US-ryan-high.onnx.json \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json"

# British English - Alan (Male, Medium)
RUN curl -L -o /app/en_GB-alan-medium.onnx \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx" && \
    curl -L -o /app/en_GB-alan-medium.onnx.json \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json"

# British English - Alba (Female, Medium)
RUN curl -L -o /app/en_GB-alba-medium.onnx \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alba/medium/en_GB-alba-medium.onnx" && \
    curl -L -o /app/en_GB-alba-medium.onnx.json \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alba/medium/en_GB-alba-medium.onnx.json"

# US English - LibriTTS (Multi-speaker, High Quality)
RUN curl -L -o /app/en_US-libritts-high.onnx \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts/high/en_US-libritts-high.onnx" && \
    curl -L -o /app/en_US-libritts-high.onnx.json \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts/high/en_US-libritts-high.onnx.json"

# Copy server, voice config, LoRA weights, and static files
COPY server.py /app/server.py
COPY voices.json /app/voices.json
COPY piper_lora_prosody.json /app/piper_lora_prosody.json
COPY static /app/static
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/entrypoint.sh"]
