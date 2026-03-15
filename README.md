# ak6mj-simplex-voice-ai

An AI-powered voice chatbot for 2m FM simplex — runs entirely on a Mac, entirely
offline. Listens on a handheld transceiver, transcribes incoming speech with Whisper,
generates responses with a local LLM, and transmits back in the operator's cloned voice.
FCC Part 97 compliant. Callsign: **AK6MJ**.

---

## Showcase

> *A station calls in on 146.555 simplex. The bot hears them, thinks, and talks back
> in the owner's voice — all without touching the internet.*

**What it can do on-air:**

- Hold a casual conversation about anything: weather, radio tech, current events
  (web search available when needed), amateur radio topics
- Remember returning operators — name, interests, past QSOs — and greet them
  personally
- Run a **radio BBS**: accept personal messages for other callsigns and announce
  them when those stations check in; post all-stations bulletins
- Answer questions about propagation, band plans, operating procedures
- Identify itself every 10 minutes per FCC §97.119, and sign off gracefully
- Respond to a voice shutdown/restart command from the control operator
- Speak all callsigns in NATO phonetics so nothing gets garbled by TTS

**What makes it unusual:**

- The voice is a clone of the station owner's voice (Qwen3-TTS + reference audio)
- The entire stack — STT, LLM, TTS — runs on Apple Silicon with no cloud API
  required (Claude API available as an optional faster backend)
- Responses stream sentence-by-sentence to the radio as they're generated,
  cutting time-to-first-audio roughly in half
- Phonetic callsign decoding handles non-standard alphabets (Beta, Baker, Able…)
  via a local extraction model fallback

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Mac (Apple Silicon)                         │
│                                                                 │
│  ┌──────────┐   USB   ┌─────────────────┐                      │
│  │ Baofeng  │─audio──►│  Digirig Mobile │                      │
│  │    HT    │◄─audio──│  (USB sound +   │                      │
│  │  2m FM   │  DTR    │   serial PTT)   │                      │
│  └──────────┘  PTT    └────────┬────────┘                      │
│       ▲                        │ 48kHz mono PCM                 │
│       │                        ▼                                │
│       │              ┌─────────────────┐                        │
│       │              │  VOX Detector   │ RMS threshold -47 dBFS │
│       │              │  (sounddevice)  │ 1.5s hang time         │
│       │              └────────┬────────┘                        │
│       │                       │ float32 audio                   │
│       │                       ▼                                 │
│       │              ┌─────────────────┐                        │
│       │              │  Whisper STT    │ mlx-whisper            │
│       │              │ large-v3-turbo  │ Apple Silicon optimized│
│       │              └────────┬────────┘                        │
│       │                       │ transcription text              │
│       │                       ▼                                 │
│       │              ┌─────────────────┐                        │
│       │              │   Compliance    │ Emergency detect       │
│       │              │   (Part 97)     │ Shutdown commands      │
│       │              └────────┬────────┘ Content filter        │
│       │                       │                                 │
│       │          ┌────────────┼────────────┐                    │
│       │          ▼            ▼            ▼                    │
│       │   ┌────────────┐ ┌────────┐ ┌──────────────┐          │
│       │   │  Memory    │ │Dialog  │ │Message Board │          │
│       │   │  Manager   │ │Manager │ │(radio BBS)   │          │
│       │   │(per-call   │ │(multi- │ │personal msgs │          │
│       │   │  JSON)     │ │ turn)  │ │+ bulletins   │          │
│       │   └─────┬──────┘ └───┬────┘ └──────┬───────┘          │
│       │         └────────────┼─────────────┘                   │
│       │                      │ context + intent                 │
│       │                      ▼                                  │
│       │           ┌─────────────────────┐                       │
│       │           │   LLM (streaming)   │ Ollama qwen3:32b      │
│       │           │  + DuckDuckGo web   │ or Claude API         │
│       │           │       search        │                       │
│       │           └──────────┬──────────┘                       │
│       │                      │ sentence stream                  │
│       │                      ▼                                  │
│       │           ┌─────────────────────┐                       │
│       │           │    Qwen3-TTS 0.6B   │ mlx-audio            │
│       │           │    (voice clone)    │ cloned voice profile  │
│       │           └──────────┬──────────┘                       │
│       │                      │ int16 audio @ 48kHz              │
│       │                      ▼                                  │
│       │           ┌─────────────────────┐                       │
│       │           │   PTT on → play →   │ DTR=High via          │
│       └───────────│      PTT off        │ pyserial              │
│                   └─────────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
```

The pipeline is **single-threaded and blocking** — this matches the half-duplex nature
of FM radio naturally. LLM responses stream sentence-by-sentence: TTS synthesizes and
transmits each sentence as it arrives while the model continues generating, cutting
perceived latency roughly in half.

A **web dashboard** (separate process, port 8080) provides a live log stream, message
board management, transcript/WAV browser, and prompt editor — without ever touching the
main radio loop.

---

## Hardware

| Component | Details |
|-----------|---------|
| Radio | Any HT with Kenwood-style 2-pin connector (Baofeng UV-5R, etc.) |
| Interface | [Digirig Mobile](https://digirig.net) — USB audio + serial PTT (VID `1209` PID `7388`) |
| PTT wiring | DTR=High → TX. macOS USB-CDC doesn't reliably deliver RTS, so only DTR is used |
| Mac | Apple Silicon (M1–M4), 64GB+ RAM recommended for qwen3:32b; 32GB workable with a smaller model |

> The [AIOC cable](https://github.com/skuep/AIOC) (VID `1209` PID `7388`) also works and
> is auto-detected by the same VID:PID. **Do not upgrade AIOC firmware past 1.1.x on
> macOS** — later firmware broke PTT via DTR.

---

## How to Use It

Once the bot is running, other stations interact with it over the air by voice.
No special equipment or software needed on their end — just a radio on the right frequency.

### General conversation

Just talk to it. Ask questions, chat about radio, request a propagation report.

```
"Hey AK6MJ, what's the KP index today?"
"AK6MJ, what's a good HF antenna for a small lot?"
"AK6MJ, can you hear me okay?"
```

The bot responds conversationally, searches the web for factual questions, and
remembers you across sessions.

### Leaving a personal message

Say something like:

```
"Leave a message for W6ABC: tell him the net is at 8pm Friday."
"Can you pass a message to KD9XYZ? Tell her I'll be on tomorrow."
"Store a message for November Six Whiskey: the repeater is back up."
```

The bot will walk you through it if anything is missing. The message is delivered
automatically the next time that callsign checks in.

### Posting a bulletin (all-stations)

```
"Post a bulletin: the club meeting is moved to the 20th."
"Can you put out a bulletin? The hilltop repeater is down for maintenance."
"Tell everyone: swap meet this Saturday at 9am, Sunnyvale."
```

The bot confirms before posting. The bulletin is announced to every station that
checks in until you say it's no longer current.

### Reading bulletins

```
"Any bulletins?"
"What's on the message board?"
"Got any announcements?"
```

### Expiring a bulletin

```
"That bulletin is no longer current."
"Get rid of the last bulletin."
"That's outdated, take it down."
```

### Control operator commands (your callsign only)

```
"AK6MJ shut down"          — graceful sign-off and exit
"AK6MJ, please restart"    — reloads the process in-place
"AK6MJ go silent"          — same as shut down
```

Natural phrasing is fine — commas, "please", variations all work.

---

## Software Stack

| Layer | Component | Notes |
|-------|-----------|-------|
| STT | [mlx-whisper](https://github.com/ml-explore/mlx-examples) `large-v3-turbo` | Apple Silicon optimized |
| LLM (default) | [Ollama](https://ollama.com) `qwen3:32b` | Fully local, `/no_think` suppresses chain-of-thought |
| LLM (alt) | Claude API `claude-opus-4-6` | Set `LLM_MODE=claude`; includes server-side web search |
| TTS | [mlx-audio](https://github.com/lucasnewman/mlx-audio) Qwen3-TTS 0.6B | Voice clone from reference audio profile |
| Web search | [duckduckgo-search](https://github.com/deedy5/duckduckgo_search) | Keyword-triggered in Ollama mode |
| Audio I/O | [sounddevice](https://python-sounddevice.readthedocs.io/) | PortAudio bindings, 48kHz mono |
| PTT | [pyserial](https://pyserial.readthedocs.io/) | DTR control via Digirig serial |

---

## Setup

### Prerequisites

- macOS on Apple Silicon
- [Miniconda](https://docs.conda.io/en/latest/miniconda.html) or Anaconda
- [Ollama](https://ollama.com) installed and running (`ollama serve`)
- Digirig Mobile or AIOC cable (for live radio; `--dry-run` works without hardware)
- A voice profile directory with `audio.wav` + `meta.json` (see **Voice Clone** below)

### Install

```bash
git clone https://github.com/bbarclay7/ak6mj-simplex-voice-ai.git
cd ak6mj-simplex-voice-ai
make setup
```

### Pull models

```bash
# Main LLM (large but best quality)
ollama pull qwen3:32b

# Small model for callsign/topic extraction (fast, low overhead)
ollama pull qwen3:4b

# Download STT and TTS models for offline use
make download-models
```

### Configure

Edit `config.yaml`:

```yaml
callsign: "N0CALL"           # your callsign
aioc:
  audio_device: "USB Audio Device"   # match your interface name (run: make monitor)
vox:
  threshold_dbfs: -47        # raise if noise triggers VOX; lower if speech doesn't
```

### Run

```bash
ollama serve                 # in a separate terminal, if not already running

make run                     # live radio (Digirig/AIOC hardware required)
make dry-run                 # system mic + speakers, no PTT — good for testing
make monitor                 # show live dBFS levels to calibrate VOX threshold
make dashboard               # web UI at http://localhost:8080
```

---

## Voice Clone

The TTS uses Qwen3-TTS with a reference voice profile. Create a directory containing:

- `audio.wav` — 5–15 seconds of clean speech from the target voice
- `meta.json`:
  ```json
  {
    "name": "Your Name",
    "transcript": "Exact word-for-word transcript of audio.wav"
  }
  ```

Set `tts.voice_profile_dir` in `config.yaml` to point to this directory.

---

## FCC Part 97 Compliance

| Rule | Implementation |
|------|---------------|
| §97.119 Station ID | NATO phonetic callsign at startup, every 10 min, and sign-off |
| §97.113 No pecuniary | System prompt + regex filter blocks commercial language |
| §97.113 No obscenity | Profanity filter on all LLM output |
| §97.109 Auto control | Voice kill switch; Ctrl+C graceful shutdown |
| Emergency traffic | "mayday" / "break break" / "pan pan" → immediate silence |
| No encryption | Plain voice FM; all processing local |

---

## Project Structure

```
main.py            Entry point — listen→respond loop, PTT, streaming transmit
audio.py           Hardware interface, VOX recorder, PTT (DTR/RTS)
compliance.py      Part 97: station ID, content filter, emergency/shutdown/restart
dialog.py          Dialog ABC + DialogManager (multi-turn framework)
stt.py             Whisper wrapper (mlx-whisper)
tts.py             Qwen3-TTS voice clone (mlx-audio)
llm.py             Ollama + DuckDuckGo web search, streaming response generator
llm_claude.py      Claude API backend with server-side web search
memory_manager.py  Per-callsign JSON profiles; phonetic callsign decoder + LLM fallback
message_board.py   Radio BBS — personal messages + bulletins; multi-turn composers
dashboard.py       FastAPI web UI (separate process, port 8080)
download_models.py Cache all HF + Ollama models for offline operation
config.yaml        All tunable parameters
Makefile           Convenience targets
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Audio device not found | Run `make monitor` — confirm device name, update `aioc.audio_device` in config |
| VOX triggers on noise | Raise `vox.threshold_dbfs` (e.g. -44); raise radio squelch to 3–5 |
| VOX never triggers | Lower `vox.threshold_dbfs`; check squelch isn't too tight |
| Transmission cuts mid-sentence | Raise `vox.hang_time_sec` (default 1.5s) |
| First syllable clipped on TX | Increase PTT settle delay in `audio.py` `ptt_on()` |
| PTT key but no audio out | Check DTR wiring; verify audio output device matches |
| Ctrl+C doesn't exit | Hit Ctrl+C a second time to force quit |

---

## License

Provided for amateur radio experimentation. You are responsible for compliance with
your country's amateur radio regulations. Operation requires a valid amateur radio license.
