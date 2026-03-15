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
