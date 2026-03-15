# AIOC Ham Radio Voice Chatbot — AK6MJ

## Summary

Python app that turns a Baofeng + AIOC cable into a Part 97-legal voice chatbot.
Listens on 2m FM, transcribes with Whisper, responds via Ollama, speaks back in
your cloned voice (Qwen3-TTS from your existing voiceclone setup).

## Architecture

```
Baofeng <--FM audio--> AIOC (USB) <---> Mac (Python)
                                         |
                        sounddevice ------+------ pyserial (PTT: DTR/RTS)
                             |
                        VOX Detect (RMS threshold)
                             |
                        STT (lightning-whisper-mlx, distil-medium.en)
                             |
                        Compliance check (emergency? shutdown cmd?)
                             |
                        Ollama (llama3.1:8b) + DuckDuckGo search
                             |
                        Content filter (Part 97)
                             |
                        Station ID if due (every 10 min)
                             |
                        TTS (Qwen3-TTS via mlx-audio, BB voice clone)
                             |
                        PTT ON → play audio → PTT OFF
```

Single-threaded blocking loop — matches half-duplex radio. No async needed.

## Files to Create

```
aioc-bot/
├── config.yaml        # Callsign, device settings, thresholds, model params
├── requirements.txt   # Python deps
├── Makefile           # conda setup + run commands
├── main.py            # Entry point, main listen→respond loop
├── audio.py           # AIOC discovery, PTT control, VOX recording
├── stt.py             # Whisper transcription (lightning-whisper-mlx)
├── tts.py             # Qwen3-TTS voice clone (reuses ../voiceclone/voices/bb/)
├── llm.py             # Ollama chat + DuckDuckGo web search
└── compliance.py      # Part 97: station ID timer, content filter, shutdown
```

## Part 97 Compliance

| Rule | Implementation |
|------|---------------|
| §97.119 Station ID | Phonetic callsign ("Alpha Kilo Six Mike Juliet") at startup, every 10 min, and sign-off |
| §97.113 No pecuniary | System prompt + regex content filter on LLM output |
| §97.113 No obscenity | System prompt + regex profanity filter |
| §97.109 Auto control | Permitted on 2m VHF; voice kill switch ("AK6MJ shut down"); Ctrl+C graceful shutdown |
| Emergency traffic | Detect "mayday"/"break break"/"pan pan" → go silent |
| No encryption | Plain voice FM, all processing is local |

## Key Technical Decisions

- **PTT**: pyserial DTR=True/RTS=False to TX, DTR=False/RTS=True to RX (AIOC serial port, VID:1209 PID:7388)
- **VOX**: Software RMS threshold on sounddevice input stream (~-30 dBFS, 1s hang time)
- **STT**: `lightning-whisper-mlx` with `distil-medium.en` — fastest on Apple Silicon, batch mode (record whole TX then transcribe)
- **TTS**: `mlx-audio` Qwen3-TTS-0.6B with voice profile from `../voiceclone/voices/bb/` (audio.wav + meta.json). Resamples output to 48kHz int16 for AIOC
- **LLM**: Ollama `llama3.1:8b`, system prompt enforces concise 1-3 sentence answers. Conversation history bounded at 10 exchanges
- **Web search**: `duckduckgo-search` triggered by keyword heuristics ("what is", "weather", "current", etc.), results injected as LLM context
- **Dry-run mode**: `--dry-run` flag uses system mic/speakers, no PTT — for testing without hardware

## Dependencies

```
sounddevice, pyserial, lightning-whisper-mlx, mlx-audio, soundfile,
numpy, ollama, duckduckgo-search, librosa, pyyaml
```

External: `ollama serve` + `ollama pull llama3.1:8b`, conda env

## Current Status (2026-03-14)

Phase 1 is **complete and hardware-tested**. All files exist and run. The bot has been
used on-air (logs show rx/tx sessions Feb–Mar 2026). Phase 2 (agent mode) not started.

Known tuning notes:
- Audio device in config.yaml is `"USB Audio Device"` (Digirig Mobile), not "AllInOneCable"
- STT model switched to `whisper-large-v3-turbo` for better accuracy
- LLM upgraded to `qwen3:32b`

## Build Order

1. `config.yaml` + `requirements.txt` + `Makefile` — project skeleton
2. `compliance.py` — safety-critical, no ML deps, easy to unit test
3. `audio.py` — AIOC discovery, PTT, VOX recorder (test with `--dry-run`)
4. `stt.py` — Whisper wrapper, test with a WAV file
5. `tts.py` — voice clone TTS, test standalone
6. `llm.py` — Ollama + web search, test standalone
7. `main.py` — wire everything, test dry-run end-to-end
8. Hardware test with AIOC + Baofeng + second radio

## Verification

- **Dry-run**: Speak into Mac mic → see transcription in logs → hear cloned voice response from speakers
- **Compliance**: Run for 20+ min, verify station ID appears in logs at correct intervals
- **Content filter**: Feed profanity/commercial text through `compliance.filter_response()`, verify redaction
- **Shutdown**: Say "AK6MJ shut down" → verify graceful sign-off and exit
- **Hardware**: Key a second radio, ask a question, verify response comes back on frequency

---

## Future: Agent Mode (Phase 2)

Current POC is a stateless Q&A bot. Phase 2 evolves it into a persistent agent with
memory, tool use, and net participation capabilities. LLM: `qwen3:32b` (already configured,
native tool calling support).

### Net Participation

- **Net check-in**: Recognize net control station (NCS) calling the net, respond with
  callsign check-in when roll is called
- **Attendance tracking**: Log each station that checks in (callsign, time, signal report)
  to a structured store (SQLite or JSON per net session)
- **Minutes / summary**: After net closes, generate a summary of topics discussed,
  stations checked in, any action items — saved to `logs/nets/` as timestamped markdown
- **Net protocol awareness**: Understand common net scripts — directed nets, roundtables,
  traffic handling. Know when to transmit vs. stand by. Recognize "go ahead", "over",
  "clear", "net closed" etc.
- **Scheduled nets**: Config for recurring nets (day/time/frequency/NCS callsign) so the
  bot knows when to switch into net mode vs. free-chat mode

### Local Knowledge RAG (Facts DB)

The LLM hallucinates specific numbers (frequencies, power limits, band edges). A local
facts database prevents this by grounding answers in verified data.

- **Band plan database**: All amateur band plans with exact frequency allocations,
  mode subbands (e.g., FT8 on 14.074, 7.074, 21.074, etc.), power limits by license
  class, band edges. Sourced from ARRL band plan charts.
- **Repeater directory**: Local repeaters with freq, offset, CTCSS tone, location
- **Common Q-codes and pro-signs**: CQ, 73, QTH, QSL, etc. with meanings
- **Part 97 quick reference**: Key rules the bot needs to know cold
- **Storage**: SQLite with FTS5 full-text search, or simple embedded vector store
  (e.g., `sqlite-vec` or `chromadb` local)
- **Retrieval**: On every question, search the facts DB first. If a match is found,
  inject it as grounding context before the LLM prompt. Facts DB takes priority over
  web search results and LLM training data.
- **Editable**: Operator can add/correct facts via a simple CLI or config file
  (`data/facts.yaml` or `data/facts/` directory of markdown files)

### Persistent Memory (Per-Callsign)

Two-tier memory system: **facts** (static, curated) and **memories** (dynamic, learned).

- **Conversation memory**: Per-callsign history stored in SQLite — remember past QSOs,
  topics discussed, personal details shared (name, QTH, rig, interests)
- **Memory extraction**: After each QSO, the LLM summarizes key facts learned about
  the other operator and stores them tagged by callsign. E.g.:
  - W6ABC: "Name is Bob, lives in San Jose, runs a Yaesu FT-991A, interested in
    satellites and POTA"
  - KI6XYZ: "New ham, studying for General, asked about antenna recommendations"
- **Recall on contact**: When the bot hears a callsign it recognizes (from STT or
  explicit "this is W6ABC"), it retrieves that operator's memory and injects it into
  the LLM context: "You are speaking with Bob (W6ABC). Last QSO was 2 weeks ago.
  He was planning a POTA activation at Big Basin."
- **Relationship graph**: Track who talks to whom, common interests, connection
  opportunities ("You and W6ABC both mentioned satellite work last week")
- **Forgetting policy**: Auto-summarize old memories to keep context manageable.
  Flag sensitive info (health, personal) for shorter retention.
  Recent QSOs kept verbatim, older ones compressed to bullet points.

### Tool Use (Ollama native tool calling)

- **Web search**: Already implemented (DuckDuckGo) — upgrade to proper tool call via
  qwen3's function calling rather than keyword heuristics
- **Propagation lookup**: Query solar flux, band conditions, MUF from hamqsl.com or
  similar APIs
- **Callsign lookup**: QRZ.com / HamDB API — look up name, QTH, license class for
  incoming callsigns
- **Weather**: Local weather for the station's QTH or a requested location
- **Repeater directory**: Look up local repeaters, offsets, tones
- **Calculator / unit conversion**: Useful for ham radio (dB, wavelength, antenna calcs)

### Speaker Identification

- **Voice fingerprinting**: Associate voice signatures with callsigns over time so the
  bot can recognize returning operators even before they identify
- **Multi-speaker segmentation**: In a net or roundtable, distinguish between different
  speakers in the same recording to correctly attribute statements

### New Files (Phase 2)

```
aioc-bot/
├── ... (existing files) ...
├── knowledge.py       # Facts DB: band plans, repeaters, Part 97 reference
├── memory.py          # Per-callsign conversation memory + recall
├── tools.py           # Tool definitions for Ollama function calling
├── net.py             # Net participation state machine + attendance/minutes
└── data/
    ├── facts.db       # Local knowledge base (SQLite + FTS5)
    ├── facts/         # Editable fact files (markdown/yaml)
    │   ├── bandplan.yaml
    │   ├── ft8.yaml
    │   ├── repeaters.yaml
    │   └── part97.yaml
    ├── memory.db      # Per-callsign memory store
    └── nets/          # Net session logs (markdown)
```

### Config Additions (Phase 2)

```yaml
# --- Agent / Memory ---
knowledge:
  facts_dir: "data/facts"     # editable fact files (yaml/markdown)
  db_path: "data/facts.db"    # indexed for search

memory:
  db_path: "data/memory.db"
  max_context_messages: 5     # past memories to inject per callsign
  retention_days: 365
  summarize_after_days: 30    # compress old QSOs to bullet points

# --- Tool Use ---
tools:
  web_search: true
  callsign_lookup: true       # QRZ/HamDB
  propagation: true           # solar/band conditions
  weather: true

# --- Net Participation ---
nets:
  - name: "Sunday Morning Net"
    day: "sunday"
    time: "09:00"
    frequency: "146.520"
    ncs_callsign: "W6XYZ"
    mode: "directed"          # directed | roundtable
```
