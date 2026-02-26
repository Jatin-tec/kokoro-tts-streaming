#!/bin/bash
set -e

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"
WORKERS="${WORKERS:-1}"

echo "[kokoro] Starting TTS streaming server on $HOST:$PORT ..."
exec uvicorn server:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    --log-level info
