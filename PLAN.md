# AIOC Ham Radio Voice Chatbot — AK6MJ

## Summary

Python app that turns a Baofeng + Digirig Mobile into a Part 97-legal voice chatbot on 2m FM.
Listens via VOX, transcribes with Whisper, responds via Ollama (qwen3:32b) or Claude API,
speaks back in the station owner's cloned voice (Qwen3-TTS). Callsign: AK6MJ.

## Architecture

```
Baofeng HT <-- 2m FM --> Digirig Mobile (USB audio + serial PTT)
                                    |
                        sounddevice (48kHz, mono)
                                    |
                          VOX Detector (RMS threshold -47 dBFS, 1s hang)
                                    |
                          STT: Whisper large-v3-turbo (mlx-whisper, Apple Silicon)
                                    |
                          Compliance check (emergency? shutdown? Part 97)
                                    |
                    ┌───────────────┴──────────────┐
                    │                              │
              Memory Manager               Message Board
          (callsign_memory/*.json)        (messages/*.json)
              per-callsign context         relay + commands
                    │                              │
                    └───────────────┬──────────────┘
                                    │
                          LLM: Ollama qwen3:32b  (or Claude API)
                               + DuckDuckGo web search
                                    │
                          Content filter + Station ID (every 10 min)
                                    │
                          TTS: Qwen3-TTS 0.6B (mlx-audio, voice clone BB)
                                    │
                          PTT ON (DTR=1 RTS=0) → play audio → PTT OFF
                                    |
                        Digirig Mobile (USB audio + serial PTT)
                                    |
                         Baofeng HT <-- 2m FM --> other stations
```

Single-threaded blocking loop — matches half-duplex radio. Dashboard runs as a separate
process and never touches the main loop.

## Current File Layout

```
aioc-bot/
├── main.py             # Entry point, listen→respond loop
├── audio.py            # Digirig discovery, PTT (DTR/RTS), VOX recorder
├── compliance.py       # Part 97: station ID, content filter, emergency/shutdown/restart
├── dialog.py           # Dialog base class + DialogManager (multi-turn framework)
├── stt.py              # Whisper wrapper (mlx-whisper)
├── tts.py              # Qwen3-TTS voice clone (mlx-audio)
├── llm.py              # Ollama + DuckDuckGo web search
├── llm_claude.py       # Claude API backend with server-side web search
├── memory_manager.py   # Per-callsign JSON profiles (callsign_memory/)
├── message_board.py    # Radio BBS: personal messages + bulletins (messages/)
│                       #   MessageComposer: multi-turn compose dialog
├── dashboard.py        # FastAPI web UI — port 8080 (separate process)
├── download_models.py  # One-shot: cache all HF + Ollama models for offline use
├── config.yaml         # All tunable parameters
├── requirements.txt
└── Makefile
```

## Hardware

| Component | Details |
|-----------|---------|
| Radio | Baofeng UV-5R (or similar), SQL 3–5, simplex |
| Interface | Digirig Mobile — USB audio (`"USB Audio Device"`) + serial PTT |
| PTT wiring | DTR=High → TX; DTR=Low → RX. RTS not used (macOS USB-CDC limitation) |
| Firmware | AIOC HW v1.0 at factory firmware — **do not upgrade** (see CLAUDE.md) |
| Mac | Apple Silicon, 128 GB RAM, macOS |

## Part 97 Compliance

| Rule | Implementation |
|------|---------------|
| §97.119 Station ID | Phonetic callsign at startup, every 10 min, and sign-off |
| §97.113 No pecuniary | System prompt + regex content filter on all LLM output |
| §97.113 No obscenity | System prompt + regex profanity filter |
| §97.109 Auto control | Permitted on 2m VHF; voice kill switch; Ctrl+C graceful shutdown |
| Emergency traffic | "mayday"/"break break"/"pan pan" → go silent immediately |
| No encryption | Plain voice FM, all processing local |

## Key Technical Decisions

- **Hardware interface**: Digirig Mobile (`"USB Audio Device"`, VID:1209 PID:7388). Auto-detected by VID:PID.
- **PTT**: pyserial DTR=True/RTS=False → TX. macOS USB-CDC does not reliably deliver RTS, so only DTR is used.
- **VOX**: RMS threshold −47 dBFS, 1s hang time, 0.5s min transmission. Muted during TX.
- **STT**: `mlx-community/whisper-large-v3-turbo` via mlx-whisper. Primed with NATO phonetics prompt.
- **LLM (default)**: Ollama `qwen3:32b`. Uses `/no_think` prefix to suppress chain-of-thought; `<think>` blocks stripped by regex.
- **LLM (alt)**: Claude API (`claude-opus-4-6`) with server-side web search tool. Set `LLM_MODE=claude`.
- **Web search**: DuckDuckGo via keyword heuristics (Ollama mode); Claude built-in tool (Claude mode).
- **TTS**: `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16` via mlx-audio. Voice profile at `../voiceclone/voices/bb/`. Output normalized to 90% peak, resampled to 48 kHz int16.
- **Memory**: Per-callsign JSON in `callsign_memory/`. Callsign + NATO phonetics extraction. Topic summarized by Claude Haiku if regex misses.
- **Message board**: Personal messages (clear on delivery) + bulletins (persist until expired). Detected from transcription; delivered as relay prefix before LLM response.
- **Offline**: `HF_HUB_OFFLINE=1` at runtime. `make download-models` caches everything once.
- **Dashboard**: FastAPI + SSE (separate process). Reads logs/, messages/, config.yaml. Never touches main loop.

## Current Status (2026-03-15)

Phase 1 **complete and on-air**. Phase 1.5 features added this session:

| Feature | Status |
|---------|--------|
| Core pipeline (VOX→STT→LLM→TTS→PTT) | ✅ hardware-tested |
| Claude API backend (llm_claude.py) | ✅ done |
| Per-callsign memory (memory_manager.py) | ✅ done |
| Radio BBS / message board | ✅ done |
| Multi-turn dialog framework (dialog.py) | ✅ done |
| Web dashboard (dashboard.py) | ✅ done |
| Bot self-restart (voice + dashboard) | ✅ done |
| Model download tooling | ✅ done |
| Callsign → NATO phonetics before TTS | ✅ done |
| Local knowledge RAG (Phase 2) | ⬜ not started |
| Net participation (Phase 2) | ⬜ not started |
| Ollama native tool calling (Phase 2) | ⬜ not started |
| Speaker ID (Phase 2) | ⬜ not started |

## Make Targets

```
make run              # bot with AIOC hardware (offline)
make run-online       # bot with HF downloads enabled (first run)
make dry-run          # system mic/speakers, no PTT
make dry-run-claude   # dry-run with Claude API
make monitor          # show live dBFS levels (calibrate VOX)
make download-models  # cache whisper + Qwen3-TTS + qwen3:32b for offline use
make dashboard        # web UI on http://localhost:8080
make clean            # remove logs
```

---

## Natural Language Robustness (Phase 1.5 — done)

All voice-command detection uses regex with word boundaries, optional "please", and tolerance
for commas/insertions from natural speech. Specific fixes applied after audit:

| Module | Pattern | Problem | Fix |
|--------|---------|---------|-----|
| compliance.py | `_is_shutdown_command` | Substring match failed on "AK6MJ, shut down" | Regex with `[\s,]*(?:please[\s,]+)?` between callsign and verb |
| compliance.py | `_is_restart_command` | Same issue | Same fix |
| message_board.py | `_EXPIRE_RE` | Missed "get rid of that", "that's outdated" | Added natural phrasings |
| message_board.py | `_READ_BULLETINS_RE` | Missed "got any bulletins", "show me bulletins" | Added more phrasings |
| message_board.py | `_CONFIRM_RE` | Missed "ok", "sure", "absolutely", "roger" | Expanded |
| message_board.py | `_CANCEL_RE` | Missed "no thank you", "drop it" | Expanded |
| message_board.py | BulletinComposer | Required exact phrasing → LLM told users exact syntax | Multi-turn dialog via `BulletinComposer(Dialog)` |

## Phase 2: Agent Mode

### Radio BBS (done — Phase 1.5)
Personal messages and bulletins — see message_board.py.

### Local Knowledge RAG (Facts DB)

LLM still hallucinates specific numbers (frequencies, band edges). A local facts DB prevents this.

- **Band plan database**: ARRL band plans, FT8/FT4/WSPR frequencies, power limits by license class
- **Repeater directory**: Local repeaters with freq, offset, CTCSS tone
- **Part 97 quick reference**: Rules the bot needs to know cold
- **Storage**: SQLite + FTS5, or `sqlite-vec` for vector search
- **Retrieval**: Search facts DB on every question; inject as grounding context (takes priority over web search)

### Net Participation

- `NetCheckinDialog(Dialog)` — subclass the dialog framework; no main loop changes needed
- Recognize NCS calling the net; check in when roll is called
- Attendance tracking (callsign, time, signal report) per session
- Post-net summary/minutes saved to `logs/nets/`
- Scheduled nets in config (day/time/frequency/NCS callsign)

### Persistent Memory (upgrade)

Current: JSON files, simple regex extraction, Haiku fallback.
Upgrade: SQLite per-callsign store, richer recall, forgetting policy.

### Tool Use (Ollama native function calling)

qwen3:32b supports native function calling. Upgrade from keyword heuristics to proper tool dispatch:
- Web search, propagation lookup, callsign lookup (QRZ/HamDB), weather, repeater directory

### Speaker Identification

Voice fingerprinting to recognize returning operators before they identify.

### New Files (Phase 2)

```
aioc-bot/
├── knowledge.py       # Facts DB (SQLite + FTS5)
├── tools.py           # Tool definitions for Ollama function calling
├── net.py             # Net participation state machine
└── data/
    ├── facts.db
    ├── facts/         # bandplan.yaml, repeaters.yaml, part97.yaml
    ├── memory.db      # upgraded per-callsign store
    └── nets/          # net session logs
```
