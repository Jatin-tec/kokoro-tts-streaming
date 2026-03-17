# Kokoro Piper TTS with Emotion Control

Enhanced Piper TTS server with LoRA-trained emotion/prosody control, multiple voices, and SSML tag handling.

## Features

✅ **6 Voice Options** - Male, Female, US & UK accents
- `en_US-lessac-high` - Lessac (US Male, High Quality) - Default
- `en_US-amy-medium` - Amy (US Female, Medium)
- `en_US-ryan-high` - Ryan (US Male, High Quality)
- `en_GB-alan-medium` - Alan (UK Male, Medium)
- `en_GB-alba-medium` - Alba (UK Female, Medium)
- `en_US-libritts-high` - LibriTTS (Multi-speaker, High Quality)

✅ **9 Emotion Presets** - Control speech rate and energy
- `happy` - 25% faster, high energy
- `sad` - 35% slower, low energy  
- `angry` - 20% faster, very high energy
- `calm` - 25% slower, steady
- `fear`, `disgust`, `neutral`, `ps` (pleasant surprise), `unknown`

✅ **SSML Tag Stripping** - Tags won't be read out loud
- Parses and removes `<speak>`, `<break>`, `<emphasis>`, etc.
- Clean text sent to Piper for synthesis

✅ **Multiple Output Formats** - PCM, WAV, MP3, Opus

✅ **Concurrent Request Handling** - Queue + Process Pool architecture

✅ **Web Interface** - Interactive testing UI with voice and emotion selection

⚠️ **SSML Limitations** - Tags are stripped but pauses/emphasis not applied (TODO)

## Quick Start

### 1. Build and Run

```bash
docker-compose up --build
```

Server runs at: **http://localhost:8000**

### 2. Test Web UI

Open http://localhost:8000 in browser:
- Try different emotions
- Test example phrases
- Adjust speed slider

### 3. API Usage

**Plain Text with Different Voices:**
```bash
# US Male (default)
curl -X POST http://localhost:8000/tts.wav \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "voice": "en_US-lessac-high", "emotion": "happy"}' \
  -o output.wav

# US Female
curl -X POST http://localhost:8000/tts.wav \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "voice": "en_US-amy-medium", "emotion": "happy"}' \
  -o output_amy.wav

# UK Male
curl -X POST http://localhost:8000/tts.wav \
  -H "Content-Type: application/json" \
  -d '{"text": "Good afternoon", "voice": "en_GB-alan-medium", "emotion": "calm"}' \
  -o output_alan.wav
```

**SSML (tags will be stripped):**
```bash
curl -X POST http://localhost:8000/tts.wav \
  -H "Content-Type: application/json" \
  -d '{"text": "<speak>Hello <break time=\"1s\"/> world</speak>", "voice": "en_US-ryan-high", "emotion": "calm"}' \
  -o output.wav
```

**All Parameters:**
```bash
curl -X POST http://localhost:8000/tts.wav \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Your text here",
    "voice": "en_US-amy-medium",
    "emotion": "happy",
    "speed": 1.2
  }' \
  -o output.wav
```

## API Endpoints

### `POST /tts`
Stream raw PCM16 audio

### `POST /tts.wav`
Stream WAV audio (recommended)

### `POST /tts.mp3`
Stream MP3 audio (transcoded via FFmpeg)

### `POST /tts.opus`
Stream Opus audio

### `GET /voices`
List all available voices with metadata

**Response:**
```json
{
  "voices": [
    {
      "id": "en_US-lessac-high",
      "name": "Lessac (US)",
      "language": "en_US",
      "quality": "high",
      "gender": "M",
      "description": "Clear professional male voice",
      "sample_rate": 22050,
      "available": true
    },
    ...
  ],
  "default": "en_US-lessac-high",
  "total": 6
}
```

### `GET /health`
Health check with model info and queue statistics

**Response:**
```json
{
  "status": "ok",
  "default_voice": "en_US-lessac-high",
  "available_voices": ["en_US-lessac-high", "en_US-amy-medium", ...],
  "sample_rate": 22050,
  "prosody_enabled": true,
  "supported_emotions": ["angry", "calm", "disgust", "fear", "happy", "neutral", "ps", "sad", "unknown"],
  "workers": 2,
  "max_queue_size": 20,
  "queue_stats": {
    "total_requests": 36,
    "completed_requests": 31,
    "failed_requests": 0,
    "rejected_requests": 5,
    "current_queue_size": 0
  }
}
```

### `GET /`
Web interface

## Request Body

```json
{
  "text": "Text to synthesize (plain or SSML)",
  "voice": "en_US-lessac-high",  // optional, default: "en_US-lessac-high"
  "emotion": "happy",             // optional, default: "neutral"
  "speed": 1.0                    // optional, default: 1.0 (range: 0.5 - 2.0)
}
```

## Available Voices

| Voice ID | Name | Language | Gender | Quality | Description |
|----------|------|----------|--------|---------|-------------|
| `en_US-lessac-high` | Lessac (US) | en_US | M | High | Clear professional male voice (default) |
| `en_US-amy-medium` | Amy (US) | en_US | F | Medium | Natural female voice |
| `en_US-ryan-high` | Ryan (US) | en_US | M | High | Expressive male voice |
| `en_GB-alan-medium` | Alan (UK) | en_GB | M | Medium | British male voice |
| `en_GB-alba-medium` | Alba (UK) | en_GB | F | Medium | British female voice |
| `en_US-libritts-high` | LibriTTS (US) | en_US | N | High | Multi-speaker neutral voice |

**Voice Selection Tips:**
- **High quality** voices: Larger models (~100MB), better naturalness
- **Medium quality** voices: Smaller models (~50MB), faster synthesis
- **US vs UK**: Choose based on desired accent
- **Gender**: Choose M (male), F (female), or N (neutral) based on your use case
```

## Architecture

```
User Input → SSML Parser → Plain Text → Piper + Emotion Config → Audio
              (strips tags)              (LoRA prosody)
```

**Two Control Layers:**
1. **SSML** (text-level) - Currently stripped to prevent reading tags aloud
2. **Emotion** (voice-level) - Baseline prosody from trained LoRA model

## Concurrency & Performance

The server uses a **hybrid Queue + Process Pool** architecture for robust concurrent request handling:

### Architecture

```
Incoming Requests → Queue (max 20) → ProcessPoolExecutor (2 workers) → Response
                      ↓
                  503 if full
```

### Configuration

- **Workers**: 2 parallel synthesis processes (configurable via `TTS_WORKERS`)
- **Queue Size**: 20 max queued requests (configurable via `TTS_QUEUE_SIZE`)
- **Memory**: ~600MB total (~300MB per worker process)
- **Throughput**: 6-10 requests/second (vs 2 req/sec single-threaded)

### Behavior

✅ **Parallel Processing** - 2 workers synthesize audio simultaneously  
✅ **Burst Handling** - Queue buffers up to 20 requests during traffic spikes  
✅ **Graceful Degradation** - Returns HTTP 503 when queue is full  
✅ **Process Isolation** - Each worker has independent model copy (no shared state)  
✅ **Queue Metrics** - Real-time stats available at `/health` endpoint

### Monitoring

Check queue status:
```bash
curl http://localhost:8000/health | jq '.queue_stats'
```

Output:
```json
{
  "total_requests": 36,
  "completed_requests": 31,
  "failed_requests": 0,
  "rejected_requests": 5,
  "current_queue_size": 0
}
```

### Testing Concurrent Requests

**10 concurrent requests:**
```bash
for i in {1..10}; do
  curl -X POST http://localhost:8000/tts.wav \
    -H "Content-Type: application/json" \
    -d '{"text":"Test request '$i'","emotion":"happy"}' \
    -o test_$i.wav --silent &
done
wait
```

**Test all voices with emotions:**
```bash
./test_voices.sh
```

This will generate audio files for all 6 voices with 4 different emotions each (24 files total).

**Overflow test (25 requests → 5 will get 503):**
```bash
for i in {1..25}; do
  curl -X POST http://localhost:8000/tts.wav \
    -H "Content-Type: application/json" \
    -d '{"text":"Overflow test","emotion":"neutral"}' \
    -o test_$i.wav -w "%{http_code}\n" --silent &
done
wait
# Expected: 20 requests succeed (200), 5 rejected (503)
```

### Environment Variables

Control concurrency behavior:
```yaml
# docker-compose.yml
environment:
  - TTS_WORKERS=2          # Number of parallel synthesis processes
  - TTS_QUEUE_SIZE=20      # Maximum queued requests before 503
```

### Performance Metrics

- **Single Request**: ~1.2s for 2-second audio
- **10 Concurrent**: First 2 complete in ~1.3s, last completes in ~6.5s
- **Queue Overhead**: Minimal (<10ms per request)
- **503 Response**: Instant rejection when queue is full
- **Memory**: 300MB per worker (600MB for 2 workers)

## Training

The emotion control was trained using:
- **Dataset**: RAVDESS + TESS emotional speech (8,480 samples, 6.16 hours)
- **Method**: LoRA fine-tuning on prosody features
- **Accuracy**: 94.52% validation accuracy
- **Parameters**: 12,368 trainable parameters
- **Platform**: Kaggle P100 GPU

Training details: See [PROSODY_README.md](PROSODY_README.md)

## Files

### Production Code
- `server.py` - FastAPI server with emotion control, multi-voice support & SSML parser
- `voices.json` - Voice configuration file (6 voices)
- `piper_lora_prosody.json` - Trained LoRA weights (364KB)
- `static/index.html` - Web interface with voice selection
- `Dockerfile` - Docker image with Piper + 6 voice models
- `docker-compose.yml` - Docker Compose configuration
- `entrypoint.sh` - Container startup script

### Testing
- `test_concurrency.sh` - Concurrent request testing script

### Training
- `training/piper-lora-training.ipynb` - Kaggle training notebook

### Documentation
- `README.md` - This file

## Model Details

- **Base Engine**: Piper v1.4.1 (VITS-based neural TTS)
- **Available Voices**: 6 voices (3 US, 2 UK, 1 multi-speaker)
- **Default Voice**: en_US-lessac-high (22050 Hz, professional quality)
- **Model Sizes**: 50-100MB per voice (ONNX format)
- **Combined Size**: ~600MB for all 6 voices
- **Latency**: ~50ms TTFB on CPU
- **Sample Rate**: 22050 Hz (all voices)

## SSML Status

Currently SSML tags are **stripped** (won't be spoken) but not **processed**:

✅ Tags removed before synthesis - no "speak", "break", "emphasis" spoken  
⚠️ Pauses (`<break>`) not inserted - requires word-level alignment (TODO)  
⚠️ Emphasis (`<emphasis>`) not applied - Piper limitation  
⚠️ Prosody (`<prosody rate="fast">`) not applied - use emotion parameter instead

## Emotion Presets

Each emotion adjusts two parameters:
- **length_scale**: Speech rate (lower = faster)
- **noise_scale**: Energy/intensity variation

```python
EMOTION_PRESETS = {
    "neutral": {"length_scale": 1.0, "noise_scale": 0.667},
    "happy": {"length_scale": 0.75, "noise_scale": 0.85},
    "sad": {"length_scale": 1.35, "noise_scale": 0.5},
    "angry": {"length_scale": 0.8, "noise_scale": 0.9},
    "fear": {"length_scale": 0.85, "noise_scale": 0.8},
    "disgust": {"length_scale": 1.15, "noise_scale": 0.55},
    "calm": {"length_scale": 1.25, "noise_scale": 0.5},
    "ps": {"length_scale": 0.9, "noise_scale": 0.75},
}
```

## Performance

- **Speed**: Real-time synthesis on CPU
- **Quality**: 22050 Hz, professional voice quality
- **Latency**: ~50ms time-to-first-byte, ~1.2s total for 2-second audio
- **Throughput**: 6-10 requests/second with 2 workers
- **Memory**: ~600MB (300MB per worker)
- **Concurrency**: 2 parallel workers + 20-request queue
- **Formats**: PCM16, WAV, MP3 (128kbps), Opus (64kbps)

## Development

### Local Development

```bash
# Install dependencies
pip install piper-tts fastapi uvicorn

# Run server
python server.py
```

### Rebuild Container

```bash
docker-compose down
docker-compose build
docker-compose up -d
```

### View Logs

```bash
docker logs piper-tts -f
```

## TODO / Roadmap

**SSML Implementation:**
- [ ] Implement SSML pause insertion (`<break>`)
- [ ] Add prosody rate override within SSML
- [ ] Implement `<say-as>` number formatting
- [ ] Add emphasis support (requires model changes)

**Performance & Scaling:**
- [x] Concurrent request handling (Process Pool)
- [x] Queue management with 503 responses
- [x] Real-time queue metrics
- [ ] Horizontal scaling (multiple container instances)
- [ ] Redis-based task queue for distributed deployment

**Features:**
- [ ] Deploy to Azure with concurrency support
- [ ] Add more emotion presets (excited, surprised, etc.)
- [ ] Support multi-language models
- [ ] Voice cloning / custom voice support

## License

Based on Piper TTS (MIT License)

## Credits

- **Piper TTS**: https://github.com/rhasspy/piper
- **Datasets**: RAVDESS, TESS emotional speech corpora
- **Training**: Kaggle P100 GPU environment
