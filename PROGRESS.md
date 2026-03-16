# PROGRESS.md — Session Log

## Sessions

- 2026-02-04: Initial hardware test; first AIOC unit had defective PTT solder joint (TRS connector). Replaced with backup. rx/tx logs show bot working end-to-end on 2m FM.
- 2026-03-09: Switched to Digirig Mobile as primary interface (`"USB Audio Device"`). VOX threshold tuned to −47 dBFS. Additional rx/tx recordings in logs/.
- 2026-03-11: Multiple QSOs logged on 146.555 simplex. STT upgraded to whisper-large-v3-turbo. LLM upgraded to qwen3:32b with /no_think. Phase 1 stable.
- 2026-03-14: Seeded CLAUDE.md, PROGRESS.md, .claude/commands/ for session continuity. Documented Digirig Mobile audio device name and firmware warning in CLAUDE.md.
- 2026-03-15: Added llm_claude.py (Claude API + server-side web search), memory_manager.py (per-callsign JSON profiles), message_board.py (radio BBS: personal messages + bulletins), download_models.py (offline model caching). Integrated all into main loop.
- 2026-03-15: Added dashboard.py — FastAPI web UI (make dashboard → localhost:8080): live SSE log stream, message board CRUD, transcript/WAV browser, prompt/model editor, about page with SVG block diagram. Updated PLAN.md to reflect current state.
- 2026-03-15: Bot self-restart: voice command ("AK6MJ restart") and dashboard button both send SIGUSR1; bot signs off, closes hardware, os.execv() restarts in-place. PID file (bot.pid) enables dashboard→bot signaling.
- 2026-03-15: Callsign TTS fix: expand_callsigns() in compliance.py replaces bare callsigns (W6ABC) with NATO phonetics before synthesis; applied in transmit() as single chokepoint.
- 2026-03-15: Multi-turn dialog framework: dialog.py adds Dialog ABC + DialogManager. MessageComposer refactored to extend Dialog — confirms before storing, handles hesitation/noise (rejects "."), allows edit before confirm, cancels on "never mind", times out after 5 turns. New dialogs (net check-in, etc.) subclass Dialog with no main loop changes needed.
- 2026-03-15: Natural language robustness audit + fixes: shutdown/restart commands now use regex tolerating commas and "please" (safety-critical fix); BulletinComposer(Dialog) replaces rigid bulletin phrasing; _EXPIRE_RE/_READ_BULLETINS_RE/_CONFIRM_RE/_CANCEL_RE all widened to handle natural speech variations.
- 2026-03-15: LLM streaming pipeline: respond_stream() yields sentences as they arrive from Ollama; transmit_stream() opens PTT once and synthesizes/plays each sentence immediately — cuts time-to-first-audio roughly in half. max_tokens reduced (200→120) and system prompt tightened to 1-2 sentence responses.
- 2026-03-15: VOX hang time increased 1.0→1.5s to tolerate natural mid-sentence pauses (e.g. "post a bulletin <pause> the weather is nice"). LLM system prompt updated to stop reciting message board syntax at users.
- 2026-03-15: Flexible phonetic callsign decoding: expanded NATO dictionary with common variants (beta→B, baker→B, able→A, dog→D, etc.); Ollama LLM fallback (qwen3:4b, temp=0, 20 tokens) when dictionary fails but phonetic words detected. Topic extraction switched from Claude Haiku to local Ollama.
- 2026-03-15: Three-layer STT noise resilience: energy pre-check (skip Whisper if RMS < −55 dBFS), no_speech_prob threshold (discard if avg > 0.6), hallucination blocklist ("Thank you.", "you", repetitive text, pure punctuation). Both thresholds tunable in config.yaml.
- 2026-03-15: Repo renamed to ak6mj-simplex-voice-ai on GitHub. MIT license added. README overhauled with showcase, block diagram, Getting Started walkthrough, on-air usage guide. .gitignore updated to exclude callsign_memory/, messages/, .claude/.
