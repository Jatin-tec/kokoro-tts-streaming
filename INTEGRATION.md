# Kokoro TTS — Integration Guide

## Base URL

```
http://<host>:8000
```

---

## Endpoints

### `GET /health`

Returns `{"status": "ok"}` — use this to check if the service is up before sending requests.

---

### `POST /tts` — Stream raw PCM audio

Streams back raw **16-bit little-endian PCM** audio at **24 kHz mono** as it is generated (true streaming, chunk by chunk).

**Request body (JSON):**

| Field   | Type    | Required | Default      | Description                        |
|---------|---------|----------|--------------|------------------------------------|
| `text`  | string  | yes      | —            | The text to synthesize             |
| `voice` | string  | no       | `"af_bella"` | Voice ID (see voices table below)  |
| `speed` | float   | no       | `1.0`        | Playback speed multiplier (e.g. `0.8` – `1.5`) |

**Example:**

```json
{
  "text": "Hello, how can I help you today?",
  "voice": "af_bella",
  "speed": 1.0
}
```

**Response:**

- `Content-Type: audio/pcm`
- Streaming body: raw PCM16 chunks, no file header
- Response headers tell you the exact format:

| Header           | Value  | Meaning                  |
|------------------|--------|--------------------------|
| `X-Sample-Rate`  | `24000`| 24 000 Hz sample rate    |
| `X-Channels`     | `1`    | Mono                     |
| `X-Bit-Depth`    | `16`   | 16-bit signed int PCM    |

**How to handle the stream:**

Read the response body as a byte stream. Each chunk is raw PCM samples — concatenate them in order to build the full audio buffer or feed them directly into a PCM playback pipeline (e.g. Web Audio API, PyAudio, PortAudio, ALSA).

---

### `POST /tts.wav` — Stream WAV audio

Same as `/tts` but prepends a **WAV header** so the output can be played directly by any audio player, `ffplay`, `curl`, etc.

- The WAV header uses `data_len = 0xFFFFFFFF` (unknown length) to support streaming — standard players (VLC, ffplay, browsers) handle this gracefully.
- `Content-Type: audio/wav`

Use this endpoint when you need a self-contained audio file or when piping output to a player.

---

### `POST /tts.mp3` — Stream MP3 audio

Returns **MP3-encoded audio** optimized for browser playback and streaming applications.

**⚡ Low Latency Streaming:** Uses FFmpeg for real-time PCM→MP3 conversion. Audio chunks are sent to the client as they're generated, resulting in **<500ms time-to-first-byte** regardless of text length.

**Request body (JSON):** Same as `/tts` and `/tts.wav`

**Response:**

- `Content-Type: audio/mpeg`
- Streaming MP3 data at **64 kbps** bitrate
- Response headers:

| Header           | Value   | Meaning                  |
|------------------|---------|--------------------------||
| `X-Audio-Format` | `mp3`   | MP3 format               |
| `X-Sample-Rate`  | `24000` | 24 000 Hz sample rate    |
| `X-Bitrate`      | `64`    | 64 kbps bitrate          |

**When to use:**

- Browser-based audio playback (HTML5 `<audio>` element)
- Streaming to mobile apps
- When you need compressed audio (smaller file size than WAV)
- Base64-encoded audio streams for web APIs
- **Low latency requirements** (first audio chunk arrives in <500ms)

---

## Available Voices

Voice IDs are prefixed with a two-letter code that determines accent and gender:

| Prefix | Accent / Gender   | Example voices                        |
|--------|-------------------|---------------------------------------|
| `af_`  | American Female   | `af_bella`, `af_sarah`, `af_nicole`   |
| `am_`  | American Male     | `am_adam`, `am_michael`               |
| `bf_`  | British Female    | `bf_emma`, `bf_isabella`              |
| `bm_`  | British Male      | `bm_george`, `bm_lewis`               |

---

## Code Examples

### curl — download as WAV file

```bash
curl -s -X POST http://localhost:8000/tts.wav \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "voice": "af_bella", "speed": 1.0}' \
  -o output.wav
```

### curl — download as MP3 file

```bash
curl -s -X POST http://localhost:8000/tts.mp3 \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "voice": "af_bella", "speed": 1.0}' \
  -o output.mp3
```

### curl — pipe directly to ffplay

```bash
curl -s -X POST http://localhost:8000/tts.wav \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world"}' | ffplay -autoexit -i -
```

### Python — stream raw PCM and play with PyAudio

```python
import requests
import pyaudio

SAMPLE_RATE = 24_000
CHANNELS = 1
CHUNK = 4096

pa = pyaudio.PyAudio()
stream = pa.open(format=pyaudio.paInt16, channels=CHANNELS, rate=SAMPLE_RATE, output=True)

with requests.post(
    "http://localhost:8000/tts",
    json={"text": "Hello world", "voice": "af_bella", "speed": 1.0},
    stream=True,
) as resp:
    resp.raise_for_status()
    for chunk in resp.iter_content(chunk_size=CHUNK):
        if chunk:
            stream.write(chunk)

stream.stop_stream()
stream.close()
pa.terminate()
```

### Python — save the WAV response to disk

```python
import requests

resp = requests.post(
    "http://localhost:8000/tts.wav",
    json={"text": "Hello world", "voice": "bm_george", "speed": 0.95},
)
resp.raise_for_status()

with open("output.wav", "wb") as f:
    f.write(resp.content)
```

### JavaScript (browser) — stream and play via Web Audio API (PCM)

```js
async function speak(text, voice = "af_bella", speed = 1.0) {
  const resp = await fetch("/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice, speed }),
  });

  const sampleRate = parseInt(resp.headers.get("X-Sample-Rate") || "24000");
  const ctx = new AudioContext({ sampleRate });
  const reader = resp.body.getReader();
  let nextStartTime = ctx.currentTime;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    // Convert raw Int16 bytes → Float32 samples
    const int16 = new Int16Array(value.buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768;
    }

    const buffer = ctx.createBuffer(1, float32.length, sampleRate);
    buffer.copyToChannel(float32, 0);

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    source.start(nextStartTime);
    nextStartTime += buffer.duration;
  }
}
```

### JavaScript (browser) — play MP3 with HTML5 Audio

```js
async function speakMP3(text, voice = "af_bella", speed = 1.0) {
  const resp = await fetch("/tts.mp3", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice, speed }),
  });

  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  
  audio.play();
  
  // Clean up object URL after playback
  audio.onended = () => URL.revokeObjectURL(url);
}
```

---

## Error Handling

| Scenario              | What happens                              | Recommended action                        |
|-----------------------|-------------------------------------------|-------------------------------------------|
| Service unavailable   | Connection refused / timeout              | Check `/health`, retry with backoff       |
| Invalid JSON body     | HTTP `422 Unprocessable Entity`           | Validate payload fields before sending   |
| Empty `text` field    | Streams no audio bytes (empty response)   | Guard against empty strings client-side  |
| Unknown voice prefix  | Falls back to American English pipeline   | Stick to documented prefixes             |

---

## Audio Format Reference

| Property     | Value                    |
|--------------|--------------------------|
| Encoding     | PCM, signed 16-bit (Int16), little-endian |
| Sample rate  | 24 000 Hz                |
| Channels     | 1 (mono)                 |
| Bit depth    | 16                       |
| MIME (`/tts`)     | `audio/pcm`         |
| MIME (`/tts.wav`) | `audio/wav`         || MIME (`/tts.mp3`) | `audio/mpeg`        |
| MP3 bitrate  | 64 kbps                  |