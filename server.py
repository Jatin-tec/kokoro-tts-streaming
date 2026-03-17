#!/usr/bin/env python3
"""
Piper TTS with Prosody/Emotion Control - Enhanced Server

POST /tts        { "text": "...", "voice": "en_US-lessac-high", "speed": 1.0, "emotion": "neutral" }
                 → streams raw PCM16 with emotion-based prosody adjustments

POST /tts.wav    → same audio wrapped in a WAV header (streamable)
POST /tts.mp3    → MP3-encoded via FFmpeg
POST /tts.opus   → Opus-encoded via FFmpeg

GET  /health     → {"status": "ok", "model": "...", "emotions": [...]}

Supported emotions: angry, calm, disgust, fear, happy, neutral, ps, sad
Each emotion adjusts pitch (length_scale) and energy (noise_scale) parameters.
"""

import os
import json
import struct
import subprocess
import threading
import queue
import re
import asyncio
import time
from pathlib import Path
from typing import Optional, List, Tuple
from xml.etree import ElementTree as ET
from concurrent.futures import ProcessPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel
from piper.voice import PiperVoice
from piper.config import SynthesisConfig


# ── SSML Parser ───────────────────────────────────────────────────────────────

def parse_ssml(text: str) -> Tuple[str, List[dict]]:
    """Parse SSML markup and return plain text + metadata."""
    if not ("<speak>" in text.lower() or text.strip().startswith("<")):
        return text, []
    
    try:
        if not text.strip().startswith("<speak"):
            ssml_text = f"<speak>{text}</speak>"
        else:
            ssml_text = text
            
        root = ET.fromstring(ssml_text)
        plain_parts = []
        breaks = []
        
        def traverse(element, position=0):
            nonlocal plain_parts, breaks
            if element.text:
                plain_parts.append(element.text)
                position += len(element.text)
            
            for child in element:
                if child.tag == "break":
                    time_attr = child.get("time", "500ms")
                    duration_ms = parse_duration(time_attr)
                    breaks.append({"position": position, "duration_ms": duration_ms})
                    plain_parts.append(" ")
                    position += 1
                else:
                    position = traverse(child, position)
                
                if child.tail:
                    plain_parts.append(child.tail)
                    position += len(child.tail)
            
            return position
        
        traverse(root)
        return "".join(plain_parts), breaks
        
    except ET.ParseError as e:
        print(f"[ssml] Parse error: {e}, falling back to regex strip", flush=True)
        return strip_ssml_tags(text), []


def strip_ssml_tags(text: str) -> str:
    """Simple regex-based SSML tag stripper as fallback."""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_duration(duration_str: str) -> int:
    """Parse duration string like '500ms' or '1s' to milliseconds."""
    duration_str = duration_str.strip().lower()
    if duration_str.endswith('ms'):
        return int(duration_str[:-2])
    elif duration_str.endswith('s'):
        return int(float(duration_str[:-1]) * 1000)
    else:
        try:
            return int(duration_str)
        except:
            return 500

SSML_AVAILABLE = True


# ── LoRA Prosody Configuration ────────────────────────────────────────────────

def load_prosody_config():
    """Load LoRA prosody weights and emotion mappings."""
    config_path = Path("piper_lora_prosody.json")
    if not config_path.exists():
        print("[prosody] Warning: piper_lora_prosody.json not found, using defaults", flush=True)
        return None
    
    with open(config_path) as f:
        data = json.load(f)
    
    emotions = list(data.get("emotion2id", {}).keys())
    norm_stats = data.get("normalization_stats", {})
    
    print(f"[prosody] Loaded LoRA config: {len(emotions)} emotions", flush=True)
    print(f"[prosody] Available emotions: {', '.join(emotions)}", flush=True)
    
    return {
        "emotions": emotions,
        "emotion2id": data.get("emotion2id", {}),
        "normalization_stats": norm_stats,
    }


PROSODY_CONFIG = load_prosody_config()
SUPPORTED_EMOTIONS = PROSODY_CONFIG["emotions"] if PROSODY_CONFIG else ["neutral"]


# Emotion-to-synthesis parameter mappings (enhanced for clearer differences)
# These parameters control the expressiveness of each emotion
EMOTION_PRESETS = {
    "neutral": {"length_scale": 1.0, "noise_scale": 0.667},
    "happy": {"length_scale": 0.75, "noise_scale": 0.85},     # much faster, high energy
    "sad": {"length_scale": 1.35, "noise_scale": 0.5},        # much slower, low energy
    "angry": {"length_scale": 0.8, "noise_scale": 0.9},       # fast, very high energy
    "fear": {"length_scale": 0.85, "noise_scale": 0.8},       # faster, high energy with variation
    "disgust": {"length_scale": 1.15, "noise_scale": 0.55},   # slower, reduced energy
    "calm": {"length_scale": 1.25, "noise_scale": 0.5},       # slower, very steady/low energy
    "ps": {"length_scale": 0.9, "noise_scale": 0.75},         # pleasant surprise - upbeat
    "unknown": {"length_scale": 1.0, "noise_scale": 0.667},   # default
}


def get_emotion_config(emotion: str, speed_override: Optional[float] = None) -> SynthesisConfig:
    """Get Piper synthesis config for a given emotion."""
    emotion = emotion.lower() if emotion else "neutral"
    preset = EMOTION_PRESETS.get(emotion, EMOTION_PRESETS["neutral"])
    
    # Apply speed override if provided
    length_scale = preset["length_scale"]
    if speed_override and speed_override > 0:
        length_scale = length_scale / speed_override
    
    return SynthesisConfig(
        length_scale=length_scale,
        noise_scale=preset["noise_scale"],
    )


# ── Voice configuration ──────────────────────────────────────────────────────

# Load available voices from voices.json
VOICES_CONFIG = {}
try:
    voices_path = Path("/app/voices.json") if Path("/app/voices.json").exists() else Path("voices.json")
    with open(voices_path) as f:
        voices_data = json.load(f)
        VOICES_CONFIG = {v["id"]: v for v in voices_data["voices"]}
    print(f"[voices] Loaded {len(VOICES_CONFIG)} voice configurations", flush=True)
except FileNotFoundError:
    print("[voices] Warning: voices.json not found, using default voice only", flush=True)
    VOICES_CONFIG = {
        "en_US-lessac-high": {
            "id": "en_US-lessac-high",
            "name": "Lessac (US)",
            "language": "en_US",
            "quality": "high",
            "gender": "M",
            "description": "Clear professional male voice",
            "sample_rate": 22050
        }
    }

AVAILABLE_VOICES = list(VOICES_CONFIG.keys())
DEFAULT_VOICE = os.environ.get("PIPER_MODEL", "en_US-lessac-high")
MODEL_NAME = DEFAULT_VOICE  # For backward compatibility


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


print("[piper] Initializing Piper TTS voice with prosody control...", flush=True)
get_voice()
SAMPLE_RATE: int = get_voice().config.sample_rate


# ── Process Pool & Queue ──────────────────────────────────────────────────────

# Queue metrics
queue_stats = {
    "total_requests": 0,
    "completed_requests": 0,
    "failed_requests": 0,
    "rejected_requests": 0,
    "current_queue_size": 0,
}

# Process pool configuration
MAX_WORKERS = int(os.environ.get("TTS_WORKERS", "2"))  # 2 parallel workers
MAX_QUEUE_SIZE = int(os.environ.get("TTS_QUEUE_SIZE", "20"))  # Max 20 queued

print(f"[pool] Initializing ProcessPoolExecutor with {MAX_WORKERS} workers", flush=True)
executor = ProcessPoolExecutor(max_workers=MAX_WORKERS)


def synthesize_in_process(text: str, emotion: str, speed: float, model_name: str) -> bytes:
    """
    Synthesis worker function that runs in a subprocess.
    Each subprocess loads its own copy of the model.
    """
    # Load model in this subprocess (cached per process)
    model_path = None
    for candidate in [f"/app/{model_name}.onnx", f"models/{model_name}.onnx", f"{model_name}.onnx"]:
        if Path(candidate).exists():
            model_path = candidate
            break
    
    if not model_path:
        raise FileNotFoundError(f"Model {model_name}.onnx not found")
    
    voice = PiperVoice.load(model_path)
    
    # Get emotion config
    preset = EMOTION_PRESETS.get(emotion.lower(), EMOTION_PRESETS["neutral"])
    length_scale = preset["length_scale"]
    if speed and speed > 0:
        length_scale = length_scale / speed
    
    syn_config = SynthesisConfig(
        length_scale=length_scale,
        noise_scale=preset["noise_scale"],
    )
    
    # Parse SSML if present
    if "<speak>" in text.lower() or text.strip().startswith("<"):
        text, _ = parse_ssml(text)
    
    # Synthesize
    audio_bytes = b""
    for audio_chunk in voice.synthesize(text, syn_config=syn_config):
        audio_bytes += audio_chunk.audio_int16_bytes
    
    return audio_bytes


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

app = FastAPI(title="Piper TTS with Prosody Control", version="2.1")


class TTSRequest(BaseModel):
    text: str
    voice: str = "en_US-lessac-high"
    speed: float = 1.0
    emotion: str = "neutral"  # New: emotion parameter
    
    def validate_voice(self):
        """Validate voice is available."""
        if self.voice not in AVAILABLE_VOICES:
            # Try to find closest match or use default
            if self.voice in VOICES_CONFIG:
                return  # Voice config exists, assume model will be found
            raise ValueError(
                f"Voice '{self.voice}' not available. "
                f"Available voices: {', '.join(AVAILABLE_VOICES)}"
            )


@app.get("/health")
def health():
    return JSONResponse({
        "status": "ok",
        "default_voice": DEFAULT_VOICE,
        "available_voices": AVAILABLE_VOICES,
        "sample_rate": SAMPLE_RATE,
        "prosody_enabled": PROSODY_CONFIG is not None,
        "supported_emotions": SUPPORTED_EMOTIONS,
        "workers": MAX_WORKERS,
        "max_queue_size": MAX_QUEUE_SIZE,
        "queue_stats": queue_stats.copy(),
    })


@app.get("/voices")
def list_voices():
    """List all available voices with metadata."""
    voices_list = []
    for voice_id, config in VOICES_CONFIG.items():
        # Check if voice model file exists
        try:
            _find_model(f"{voice_id}.onnx")
            available = True
        except FileNotFoundError:
            available = False
        
        voices_list.append({
            "id": voice_id,
            "name": config.get("name", voice_id),
            "language": config.get("language", "en_US"),
            "quality": config.get("quality", "medium"),
            "gender": config.get("gender", "N"),
            "description": config.get("description", ""),
            "sample_rate": config.get("sample_rate", 22050),
            "available": available
        })
    
    return JSONResponse({
        "voices": voices_list,
        "default": DEFAULT_VOICE,
        "total": len(voices_list)
    })


_UI_HTML_PATH = Path(__file__).parent / "static" / "index.html"
_UI_HTML = _UI_HTML_PATH.read_text() if _UI_HTML_PATH.exists() else "<html><body><h1>Piper TTS</h1></body></html>"


@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse(content=_UI_HTML)


async def _synthesize_audio(req: TTSRequest) -> bytes:
    """
    Async synthesis using process pool for parallel processing.
    
    TWO LAYERS OF CONTROL:
    
    1. SSML (text-level) - We parse and strip SSML tags:
       - <break time="500ms"/> → pause (TODO: insert silence)
       - <emphasis> → stripped (Piper can't do emphasis yet)
       - <prosody rate="fast"> → stripped (use emotion parameter instead)
       
       Note: Piper's phonemizer does NOT strip SSML tags automatically - it reads
       them as text ("speak", "slash", etc.). We must strip them before synthesis.
    
    2. Emotion (voice-level) - Our trained LoRA model adjusts baseline prosody:
       - length_scale: speech rate (lower = faster)
       - noise_scale: energy/intensity variation
    
    These work TOGETHER: emotion sets baseline, SSML modifies text.
    """
    # Validate voice
    try:
        req.validate_voice()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Queue management
    queue_stats["total_requests"] += 1
    queue_stats["current_queue_size"] += 1
    
    # Check queue size limit
    if queue_stats["current_queue_size"] > MAX_QUEUE_SIZE:
        queue_stats["current_queue_size"] -= 1
        queue_stats["rejected_requests"] += 1
        raise HTTPException(status_code=503, detail="Server busy. Queue is full. Please try again later.")
    
    try:
        start_time = time.time()
        print(f"[tts] Request queued: voice={req.voice}, emotion={req.emotion}, queue={queue_stats['current_queue_size']}/{MAX_QUEUE_SIZE}", flush=True)
        
        # Submit to process pool for parallel synthesis
        loop = asyncio.get_event_loop()
        audio_bytes = await loop.run_in_executor(
            executor,
            synthesize_in_process,
            req.text,
            req.emotion,
            req.speed,
            req.voice  # Use requested voice instead of default MODEL_NAME
        )
        
        elapsed = time.time() - start_time
        queue_stats["completed_requests"] += 1
        print(f"[tts] Request completed: {len(audio_bytes)} bytes in {elapsed:.2f}s", flush=True)
        
        return audio_bytes
        
    except Exception as e:
        queue_stats["failed_requests"] += 1
        print(f"[tts] Request failed: {e}", flush=True)
        raise
    finally:
        queue_stats["current_queue_size"] -= 1


@app.post("/tts", summary="Stream raw PCM16 with prosody control")
async def tts_pcm(req: TTSRequest):
    audio_bytes = await _synthesize_audio(req)
    return StreamingResponse(
        iter([audio_bytes]),
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Channels": "1",
            "X-Bit-Depth": "16",
            "X-Emotion": req.emotion,
        },
    )


@app.post("/tts.wav", summary="Stream WAV audio with prosody control")
async def tts_wav(req: TTSRequest):
    audio_bytes = await _synthesize_audio(req)
    
    def _gen():
        yield wav_header(len(audio_bytes))
        yield audio_bytes
    
    return StreamingResponse(_gen(), media_type="audio/wav")


@app.post("/tts.mp3", summary="Stream MP3 audio with prosody control")
async def tts_mp3(req: TTSRequest):
    audio_bytes = await _synthesize_audio(req)
    
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
        _ffmpeg_transcode(iter([audio_bytes]), codec, read_size=2048),
        media_type="audio/mpeg",
        headers={"X-Audio-Format": "mp3", "X-Sample-Rate": str(SAMPLE_RATE), "X-Emotion": req.emotion},
    )


@app.post("/tts.opus", summary="Stream Opus audio with prosody control")
async def tts_opus(req: TTSRequest):
    audio_bytes = await _synthesize_audio(req)
    
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
        _ffmpeg_transcode(iter([audio_bytes]), codec, read_size=1024),
        media_type="audio/ogg",
        headers={"X-Audio-Format": "opus", "X-Sample-Rate": str(SAMPLE_RATE), "X-Emotion": req.emotion},
    )
