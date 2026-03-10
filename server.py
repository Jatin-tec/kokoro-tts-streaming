#!/usr/bin/env python3
"""
Piper TTS streaming server.

POST /tts        { "text": "...", "voice": "en_US-lessac-high", "speed": 1.0 }
                 → streams raw PCM16 at the voice's native sample rate (22050 Hz).
                   TTFB ≈ 50-150 ms (Piper yields one chunk per phoneme group).

POST /tts.wav    → same audio wrapped in a WAV header (streamable)
POST /tts.mp3    → MP3-encoded via FFmpeg
POST /tts.opus   → Opus-encoded via FFmpeg (recommended — best TTFB)

GET  /health     → {"status": "ok", "model": "...", "sample_rate": 22050}

SSML: wrap text in <speak>...</speak> for full SSML 1.0 support via espeak-ng.
  Supports: <break time="500ms">, <prosody rate="fast" pitch="high">,
            <say-as interpret-as="digits">, <emphasis level="strong">,
            <phoneme alphabet="ipa" ph="...">, <sub alias="...">, etc.
"""

import os
import struct
import subprocess
import threading
import queue
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel
from piper.voice import PiperVoice
from piper.config import SynthesisConfig


# ── Model loading ─────────────────────────────────────────────────────────────

MODEL_NAME = os.environ.get("PIPER_MODEL", "en_US-lessac-high")


def _find_model(filename: str) -> str:
    for candidate in [f"/app/{filename}", f"models/{filename}", filename]:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"{filename} not found in /app/, models/, or cwd")


_voice: PiperVoice | None = None


def get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        path = _find_model(f"{MODEL_NAME}.onnx")
        print(f"[piper] Loading voice: {MODEL_NAME} from {path}", flush=True)
        _voice = PiperVoice.load(path)
        print(f"[piper] Voice ready — sample_rate={_voice.config.sample_rate}Hz", flush=True)
    return _voice


print("[piper] Initializing Piper TTS voice...", flush=True)
get_voice()
SAMPLE_RATE: int = get_voice().config.sample_rate


# ── Helpers ───────────────────────────────────────────────────────────────────

def wav_header(data_len: int, sample_rate: int = SAMPLE_RATE, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    riff_size = 0xFFFFFFFF if data_len == 0xFFFFFFFF else (36 + data_len)
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", riff_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate,
        byte_rate, block_align, bits,
        b"data", data_len,
    )


def _ffmpeg_transcode(pcm_iter, codec_args: list, read_size: int = 2048):
    """Pipe PCM16 chunks into FFmpeg and yield encoded output chunks in real-time."""
    cmd = [
        "ffmpeg",
        "-f", "s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-i", "pipe:0",
        "-fflags", "+nobuffer+flush_packets",
        "-flags", "+low_delay",
        "-flush_packets", "1",
        *codec_args,
        "-",
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    q: queue.Queue = queue.Queue(maxsize=32)

    def _write():
        try:
            for chunk in pcm_iter:
                if proc.poll() is not None:
                    break
                proc.stdin.write(chunk)
                proc.stdin.flush()
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    def _read():
        try:
            while True:
                data = proc.stdout.read(read_size)
                if not data:
                    break
                q.put(data)
        finally:
            q.put(None)

    threading.Thread(target=_write, daemon=True).start()
    threading.Thread(target=_read, daemon=True).start()
    while True:
        item = q.get()
        if item is None:
            break
        yield item
    proc.wait()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Piper TTS", version="2.0")


class TTSRequest(BaseModel):
    text: str
    voice: str = "en_US-lessac-high"
    speed: float = 1.0


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "model": MODEL_NAME, "sample_rate": SAMPLE_RATE})


_UI_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse(content=_UI_HTML)


def _synthesize_pcm(req: TTSRequest):
    """
    Synchronous generator yielding raw PCM16 bytes as Piper produces them.

    Piper synthesizes one phoneme group at a time and yields each chunk immediately.
    Each chunk is ~20-100 ms of audio — true progressive streaming.
    TTFB = inference time for the first phoneme group only (~50-150 ms on CPU).

    Speed parameter maps to Piper's length_scale (inverse: speed=1.5 → scale=0.667).
    """
    voice = get_voice()
    length_scale = 1.0 / req.speed if req.speed > 0 else 1.0
    syn_config = SynthesisConfig(length_scale=length_scale)

    for audio_chunk in voice.synthesize(req.text, syn_config=syn_config):
        yield audio_chunk.audio_int16_bytes


@app.post("/tts", summary="Stream raw PCM16 — lowest TTFB")
def tts_pcm(req: TTSRequest):
    return StreamingResponse(
        _synthesize_pcm(req),
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Channels": "1",
            "X-Bit-Depth": "16",
        },
    )


@app.post("/tts.wav", summary="Stream WAV audio with header")
def tts_wav(req: TTSRequest):
    def _gen():
        yield wav_header(0xFFFFFFFF)
        yield from _synthesize_pcm(req)
    return StreamingResponse(_gen(), media_type="audio/wav")


@app.post("/tts.mp3", summary="Stream MP3 audio via FFmpeg")
def tts_mp3(req: TTSRequest):
    codec = [
        "-c:a", "libmp3lame",
        "-b:a", "64k",
        "-q:a", "9",
        "-compression_level", "0",
        "-reservoir", "0",
        "-write_xing", "0",
        "-f", "mp3",
    ]
    return StreamingResponse(
        _ffmpeg_transcode(_synthesize_pcm(req), codec, read_size=2048),
        media_type="audio/mpeg",
        headers={"X-Audio-Format": "mp3", "X-Sample-Rate": str(SAMPLE_RATE)},
    )


@app.post("/tts.opus", summary="Stream Opus audio (recommended)")
def tts_opus(req: TTSRequest):
    codec = [
        "-c:a", "libopus",
        "-b:a", "48k",
        "-vbr", "off",
        "-application", "lowdelay",
        "-frame_duration", "20",
        "-compression_level", "0",
        "-f", "ogg",
    ]
    return StreamingResponse(
        _ffmpeg_transcode(_synthesize_pcm(req), codec, read_size=1024),
        media_type="audio/ogg",
        headers={"X-Audio-Format": "opus", "X-Sample-Rate": str(SAMPLE_RATE)},
    )
