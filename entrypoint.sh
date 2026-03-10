#!/bin/bash
set -e

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"
WORKERS="${WORKERS:-1}"

# ── ONNX / OpenMP CPU threading ─────────────────────────────────────────────
# Piper uses ONNX Runtime internally; expose all cores for intra-op parallelism.
# OMP_WAIT_POLICY=active keeps threads spinning for lower wake-up latency.
CPU_COUNT=$(nproc 2>/dev/null || echo 2)
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-$CPU_COUNT}
export MKL_NUM_THREADS=${MKL_NUM_THREADS:-$CPU_COUNT}
export OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-$CPU_COUNT}
export OMP_WAIT_POLICY=${OMP_WAIT_POLICY:-active}
echo "[piper] CPU cores: $CPU_COUNT | OMP_NUM_THREADS=$OMP_NUM_THREADS | OMP_WAIT_POLICY=$OMP_WAIT_POLICY"

echo "[piper] Starting TTS streaming server on $HOST:$PORT ..."
exec uvicorn server:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    --log-level info
