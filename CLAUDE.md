# CLAUDE.md — AIOC Ham Radio Voice Chatbot

## What this is
Python app that turns a Baofeng + AIOC/Digirig cable into a Part 97-legal voice chatbot on 2m FM.
Listens via VOX, transcribes with Whisper, responds via Ollama (qwen3:32b) or Claude API,
speaks back in cloned voice (Qwen3-TTS). Callsign: AK6MJ.

## Stack
- Python 3.12, conda env `aioc-bot`
- sounddevice, pyserial, lightning-whisper-mlx, mlx-audio, ollama, duckduckgo-search
- Ollama must be running: `ollama serve` (model: qwen3:32b)
- `HF_HUB_OFFLINE=1` is set — no HuggingFace network calls at runtime
- Voice profile: `../voiceclone/voices/bb/` — do not change this path

## Hard invariants
- **Single-threaded blocking loop** — half-duplex radio, no async, ever
- **VOX mutes during TX** — `vox.mute()` before PTT on, `vox.unmute()` after PTT off, or the bot hears itself
- **PTT timing** — minimum 0.3s settle after `ptt_on()` before audio plays; increase to 0.5–0.8s for repeaters with CTCSS
- **Part 97 compliance before every TX** — `compliance.should_respond()` and `compliance.filter_response()` are not optional
- **Station ID every 10 min** — §97.119; phonetic: "Alpha Kilo Six Mike Juliet"; handled by `ComplianceManager`
- **Emergency abort** — "mayday"/"break break"/"pan pan" → go silent immediately
- **Voice kill switch** — "AK6MJ shut down" → graceful sign-off and exit
- **No emojis/URLs/special chars in LLM output** — TTS reads them literally over the air

## Key files
- `main.py` — entry point, main listen→respond loop
- `audio.py` — AIOC/Digirig discovery, PTT (DTR/RTS via pyserial VID:1209 PID:7388), VOX recorder
- `compliance.py` — Part 97: station ID timer, content filter, shutdown logic
- `stt.py` — Whisper wrapper (lightning-whisper-mlx)
- `tts.py` — Qwen3-TTS voice clone; normalizes peak to 0.9 before TX
- `llm.py` — Ollama + DuckDuckGo search (keyword-heuristic triggered)
- `llm_claude.py` — Claude API backend (set `LLM_MODE=claude` or `llm_mode: claude` in config)
- `memory_manager.py` — per-callsign JSON memory in `callsign_memory/`
- `message_board.py` — radio BBS: personal messages + bulletins (stored in `messages/`)
- `config.yaml` — all tunable parameters

## How to run / test
```bash
make dry-run          # system mic/speakers, no PTT — primary dev/test mode
make dry-run-claude   # same but Claude API backend
make monitor          # show live dBFS levels to calibrate VOX threshold
make run              # requires AIOC/Digirig hardware
```

## Common mistakes to avoid
- Don't introduce `asyncio` or threads into the main loop — half-duplex radio serializes everything naturally
- Don't change the TTS voice profile path — `../voiceclone/voices/bb/` is relative to this project dir
- Don't remove the `vox.mute()` / `vox.unmute()` sandwich in `transmit()` — bot will hear its own TX
- Don't skip `compliance.filter_response()` even in dry-run — it's the content safety gate
- PTT pin mapping: TX = DTR True / RTS False; RX = DTR False / RTS True. Don't swap.
- Audio device name is `"USB Audio Device"` (Digirig Mobile) — `HARDWARE_TEST.md` says "AllInOneCable" which is outdated

## Phase 2 (not yet started)
Net participation, local knowledge RAG (band plans, repeaters), per-callsign memory (SQLite),
tool use via Ollama native function calling, speaker ID. See PLAN.md for full spec.
