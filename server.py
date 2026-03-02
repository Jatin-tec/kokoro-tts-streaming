#!/usr/bin/env python3
"""
Kokoro TTS streaming server.
POST /tts   { "text": "...", "voice": "af_bella", "speed": 1.0 }
  → streams raw 24 kHz mono PCM (16-bit little-endian) back to the client.

POST /tts.wav
  → same but prepends a WAV header so any audio player / curl can consume it.

GET  /health
  → {"status": "ok"}
"""

import struct
import io
from pathlib import Path
import numpy as np
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel
from pydub import AudioSegment

# ── Load Kokoro pipeline once at startup ────────────────────────────────────
from kokoro import KPipeline

# Pre-warm with both language codes so the first request is fast
_pipelines: dict[str, KPipeline] = {}

def get_pipeline(lang_code: str) -> KPipeline:
    if lang_code not in _pipelines:
        _pipelines[lang_code] = KPipeline(lang_code=lang_code)
    return _pipelines[lang_code]

# Warm American English on startup
print("[kokoro] Warming up pipeline …")
get_pipeline("a")
print("[kokoro] Pipeline ready.")

SAMPLE_RATE = 24_000  # Kokoro outputs 24 kHz


# ── Helpers ─────────────────────────────────────────────────────────────────

def voice_to_lang(voice: str) -> str:
    """Map voice prefix to KPipeline lang_code."""
    prefix = voice[:2].lower()
    mapping = {"af": "a", "am": "a", "bf": "b", "bm": "b"}
    return mapping.get(prefix, "a")


def wav_header(data_len: int, sample_rate: int = SAMPLE_RATE, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    # When data_len is unknown (streaming), set RIFF chunk size to 0xFFFFFFFF too.
    riff_size = 0xFFFFFFFF if data_len == 0xFFFFFFFF else (36 + data_len)
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,          # PCM chunk size
        1,           # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_len,
    )


def audio_to_pcm16(audio: np.ndarray) -> bytes:
    """Convert float32 numpy array → int16 PCM bytes."""
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()


def pcm16_to_mp3(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convert PCM16 bytes to MP3 using pydub."""
    audio_segment = AudioSegment(
        data=pcm_bytes,
        sample_width=2,  # 16-bit = 2 bytes
        frame_rate=sample_rate,
        channels=1
    )
    
    mp3_buffer = io.BytesIO()
    audio_segment.export(mp3_buffer, format="mp3", bitrate="64k")
    return mp3_buffer.getvalue()


# ── API ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Kokoro TTS", version="1.0")


class TTSRequest(BaseModel):
    text: str
    voice: str = "af_bella"
    speed: float = 1.0


@app.get("/health")
def health():
    return JSONResponse({"status": "ok"})


_UI_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse(content=_UI_HTML)


def _stream_pcm(req: TTSRequest):
    """Generator that yields PCM16 chunks as Kokoro produces them."""
    lang = voice_to_lang(req.voice)
    pipeline = get_pipeline(lang)
    for _gs, _ps, audio in pipeline(req.text, voice=req.voice, speed=req.speed):
        if audio is not None and len(audio) > 0:
            yield audio_to_pcm16(np.array(audio))


@app.post("/tts", summary="Stream raw PCM16 @ 24 kHz mono")
def tts_pcm(req: TTSRequest):
    return StreamingResponse(
        _stream_pcm(req),
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Channels": "1",
            "X-Bit-Depth": "16",
        },
    )


@app.post("/tts.wav", summary="Stream WAV audio (with header)")
def tts_wav(req: TTSRequest):
    """
    Collects all PCM chunks, prepends a WAV header.
    Useful for curl / ffplay / direct download.
    For true streaming WAV, the data_len in the header is set to 0xFFFFFFFF
    (unknown length), which most players can handle.
    """
    def _wav_stream():
        # Unknown-length WAV header — works fine with ffplay, VLC, etc.
        yield wav_header(0xFFFFFFFF)
        yield from _stream_pcm(req)

    return StreamingResponse(
        _wav_stream(),
        media_type="audio/wav",
    )


@app.post("/tts.mp3", summary="Stream MP3 audio")
def tts_mp3(req: TTSRequest):
    """
    Streams MP3-encoded audio suitable for browser playback.
    Collects PCM chunks and converts them to MP3 format.
    """
    def _stream_mp3():
        # Collect all PCM chunks first for better MP3 quality
        pcm_chunks = []
        lang = voice_to_lang(req.voice)
        pipeline = get_pipeline(lang)
        
        for _gs, _ps, audio in pipeline(req.text, voice=req.voice, speed=req.speed):
            if audio is not None and len(audio) > 0:
                pcm_chunks.append(audio_to_pcm16(np.array(audio)))
        
        # Convert all PCM to MP3 in one go for valid MP3 stream
        if pcm_chunks:
            full_pcm = b''.join(pcm_chunks)
            mp3_data = pcm16_to_mp3(full_pcm)
            yield mp3_data
    
    return StreamingResponse(
        _stream_mp3(),
        media_type="audio/mpeg",
        headers={
            "X-Audio-Format": "mp3",
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Bitrate": "64",
        },
    )
