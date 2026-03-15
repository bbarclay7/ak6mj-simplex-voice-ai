#!/usr/bin/env python3
"""AIOC Ham Radio Voice Chatbot — AK6MJ

Listens on 2m FM via AIOC cable, transcribes with Whisper, responds via Ollama,
speaks back in cloned voice. FCC Part 97 compliant.

Usage:
    python main.py              # requires AIOC hardware
    python main.py --dry-run    # uses system mic/speakers, no PTT
"""

import argparse
import logging
import os
import signal
import sys
import time

# Use cached models only — no network requests to HuggingFace
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import soundfile as sf
import yaml

from audio import AIOC, VOXRecorder, play_audio
from compliance import ComplianceManager, expand_callsigns
from memory_manager import MemoryManager, find_callsigns
from dialog import DialogManager
from message_board import MessageBoard, MessageComposer, BulletinComposer
from stt import STT
from tts import TTS

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(log_dir: str, level: str = "INFO"):
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join(log_dir, f"bot_{time.strftime('%Y%m%d')}.log")
            ),
        ],
    )


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def save_wav(log_dir: str, label: str, audio: np.ndarray, sr: int):
    """Save a transmission as timestamped WAV for logging."""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"{label}_{time.strftime('%Y%m%d-%H%M%S')}.wav")
    sf.write(path, audio, sr)
    return path


def transmit(aioc: AIOC, tts: TTS, text: str, log_dir: str | None = None,
             vox: VOXRecorder | None = None):
    """Synthesize text and transmit via AIOC. Mutes VOX to avoid self-trigger."""
    logger = logging.getLogger("main")
    text = expand_callsigns(text)  # bare callsigns → NATO phonetics before TTS
    audio = tts.synthesize_for_radio(text, target_sr=aioc.sample_rate)
    if len(audio) == 0:
        logger.error("TTS produced no audio, skipping transmission.")
        return

    if log_dir:
        save_wav(log_dir, "tx", audio, aioc.sample_rate)

    if vox:
        vox.mute()

    duration = len(audio) / aioc.sample_rate
    logger.info(f"TX ({duration:.1f}s): {text!r}")
    aioc.ptt_on()
    play_audio(audio, aioc.sample_rate, aioc)
    aioc.ptt_off()

    if vox:
        time.sleep(0.5)  # brief pause before resuming VOX
        vox.unmute()


def transmit_stream(aioc: AIOC, tts: TTS, text_iter, log_dir: str | None = None,
                    vox: VOXRecorder | None = None) -> str:
    """Synthesize and play a stream of text chunks with a single PTT press.

    Each chunk is synthesized and played as soon as it arrives, so the first
    sentence hits the air while later sentences are still being generated.
    Returns the full transmitted text (for memory logging).
    """
    logger = logging.getLogger("main")
    if vox:
        vox.mute()

    aioc.ptt_on()
    audio_pieces = []
    text_pieces = []

    try:
        for chunk in text_iter:
            chunk = expand_callsigns(chunk)
            if not chunk:
                continue
            audio = tts.synthesize_for_radio(chunk, target_sr=aioc.sample_rate)
            if len(audio) == 0:
                continue
            duration = len(audio) / aioc.sample_rate
            logger.info(f"TX chunk ({duration:.1f}s): {chunk!r}")
            text_pieces.append(chunk)
            audio_pieces.append(audio)
            play_audio(audio, aioc.sample_rate, aioc)
    except Exception as e:
        logger.error(f"transmit_stream error: {e}")
    finally:
        aioc.ptt_off()
        if vox:
            time.sleep(0.5)
            vox.unmute()

    full_text = " ".join(text_pieces)
    if log_dir and audio_pieces:
        save_wav(log_dir, "tx", np.concatenate(audio_pieces), aioc.sample_rate)

    return full_text


def main():
    parser = argparse.ArgumentParser(description="AIOC Ham Radio Chatbot")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use system mic/speakers, no PTT")
    parser.add_argument("--monitor", action="store_true",
                        help="Just show audio levels (for calibrating VOX threshold)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    config = load_config(args.config)
    dry_run = args.dry_run or config.get("dry_run", False)
    log_dir = config.get("log_dir", "logs")
    log_tx = config.get("log_transmissions", True)

    setup_logging(log_dir, args.log_level)
    logger = logging.getLogger("main")

    logger.info(f"=== AIOC Ham Radio Chatbot — {config['callsign']} ===")
    logger.info(f"Dry run: {dry_run}")

    # Write PID file so dashboard can signal us
    pid_file = "bot.pid"
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    # --- Initialize hardware first (needed for monitor mode) ---
    aioc = AIOC(config, dry_run=dry_run)
    aioc.open()

    # --- Monitor mode: just show audio levels, skip ML models ---
    if args.monitor:
        from audio import monitor_levels
        logger.info("Monitor mode — showing audio levels. Ctrl+C to stop.")
        logger.info(f"Current VOX threshold: {config['vox']['threshold_dbfs']} dBFS")
        try:
            monitor_levels(aioc)
        except KeyboardInterrupt:
            pass
        aioc.close()
        return

    # --- Initialize ML modules (slow — loads models lazily) ---
    vox = VOXRecorder(aioc, config)
    stt = STT(config)
    tts = TTS(config)
    llm_mode = os.environ.get("LLM_MODE") or config.get("llm_mode", "ollama")
    if llm_mode == "claude":
        from llm_claude import LLMClaude
        llm = LLMClaude(config)
    else:
        from llm import LLM
        llm = LLM(config)
    logger.info(f"LLM mode: {llm_mode}")
    memory = MemoryManager(config)
    message_board = MessageBoard(config)
    dialogs = DialogManager()
    compliance = ComplianceManager(config)

    # --- Graceful shutdown on Ctrl+C ---
    _signal_count = [0]

    def handle_signal(signum, frame):
        if signum == signal.SIGUSR1:
            logger.info("SIGUSR1 received — restarting.")
            compliance.request_restart()
            vox.stop()
            return
        _signal_count[0] += 1
        if _signal_count[0] >= 2:
            logger.info("Force quit.")
            aioc.close()
            sys.exit(1)
        logger.info("Signal received, shutting down... (hit Ctrl+C again to force quit)")
        compliance.request_shutdown()
        vox.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGUSR1, handle_signal)

    logger.info("Listening... (Ctrl+C to stop)")

    try:
        # Initial station ID
        id_text = compliance.get_id_text()
        transmit(aioc, tts, id_text, log_dir if log_tx else None, vox=vox)
        compliance.mark_id_sent()

        # Track which callsigns have received bulletin announcements this session
        _bulletin_seen: set[str] = set()

        # --- Main loop ---
        while not compliance.is_shutdown:
            # 1. Wait for incoming transmission
            rx_audio = vox.wait_for_transmission()

            if rx_audio is None or compliance.is_shutdown:
                continue

            if log_tx:
                save_wav(log_dir, "rx", rx_audio, aioc.sample_rate)

            # 2. Transcribe
            logger.info("Transcribing...")
            transcription = stt.transcribe(rx_audio, aioc.sample_rate)
            if not transcription:
                logger.info("Empty transcription, ignoring.")
                continue

            # 3. Check compliance / should we respond?
            if not compliance.should_respond(transcription):
                if compliance.is_shutdown:
                    break
                continue

            # 3b. Extract callsigns; load memory context
            heard_calls = find_callsigns(
                transcription,
                exclude={config["callsign"].upper()},
                model=memory.extraction_model,
            )
            if heard_calls:
                logger.info(f"Callsigns heard: {heard_calls}")
            memory_context = memory.get_context(heard_calls)

            # If any heard callsign is new (no prior QSO on record), hint to the
            # LLM to briefly mention the message board — but not the full syntax.
            if heard_calls and any(memory.load(cs) is None for cs in heard_calls):
                memory_context += (
                    "[First contact with this station. Near the end of your response, "
                    "mention in one short sentence that a message board is available "
                    "and they can ask how to use it.]\n"
                )

            # 3c. Active dialog takes priority over LLM for this turn
            if dialogs.active:
                reply = dialogs.process(transcription, heard_calls)
                reply = compliance.filter_response(reply)
                if compliance.id_due():
                    reply = f"{compliance.get_id_text()} {reply}"
                    compliance.mark_id_sent()
                transmit(aioc, tts, reply, log_dir if log_tx else None, vox=vox)
                continue

            # Check for new message-board commands (may start a dialog)
            mb_intent = message_board.parse_intent(transcription, heard_calls)
            if mb_intent:
                if mb_intent["action"] == "compose_start":
                    composer = MessageComposer(message_board)
                    reply = composer.begin(
                        mb_intent["from"], mb_intent.get("to"), mb_intent.get("text")
                    )
                    dialogs.begin(composer)
                elif mb_intent["action"] == "compose_start_bulletin":
                    composer = BulletinComposer(message_board)
                    reply = composer.begin(mb_intent["from"], mb_intent.get("text"))
                    dialogs.begin(composer)
                else:
                    reply = message_board.handle_command(mb_intent)
                reply = compliance.filter_response(reply)
                if compliance.id_due():
                    reply = f"{compliance.get_id_text()} {reply}"
                    compliance.mark_id_sent()
                transmit(aioc, tts, reply, log_dir if log_tx else None, vox=vox)
                continue

            # 3d. Relay any pending personal messages to heard callsigns
            personal_relay = message_board.personal_relay_text(heard_calls)
            # 3e. Relay active bulletins (once per callsign per session)
            bulletin_relay = message_board.bulletin_relay_text(_bulletin_seen, heard_calls)

            # 4–7. Stream LLM response → TTS → radio sentence by sentence.
            # PTT goes on before the first sentence hits the air; each sentence
            # is synthesized and played as it arrives from the LLM.
            logger.info("Generating response (streaming)...")

            relay_prefix = " ".join(filter(None, [personal_relay, bulletin_relay]))

            def _response_stream():
                # Fixed prefix (relay messages + station ID) transmitted first
                prefix_parts = []
                if relay_prefix:
                    prefix_parts.append(compliance.filter_response(relay_prefix))
                if compliance.id_due():
                    prefix_parts.append(compliance.get_id_text())
                    compliance.mark_id_sent()
                if prefix_parts:
                    yield " ".join(prefix_parts)
                # Stream LLM sentences through compliance filter
                for sentence in llm.respond_stream(transcription,
                                                   memory_context=memory_context):
                    filtered = compliance.filter_response(sentence)
                    if filtered:
                        yield filtered

            response_text = transmit_stream(
                aioc, tts, _response_stream(), log_dir if log_tx else None, vox=vox
            )

            # 8. Update memory in background (non-blocking)
            memory.record_qso_async(heard_calls, transcription, response_text)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        if _signal_count[0] < 2:
            try:
                if compliance.is_restart:
                    signoff = f"{compliance.get_id_text()} Restarting, back in a moment."
                else:
                    signoff = f"{compliance.get_id_text()} Going silent."
                transmit(aioc, tts, signoff, log_dir if log_tx else None, vox=vox)
            except Exception:
                logger.exception("Failed to transmit sign-off")
        aioc.close()
        try:
            os.unlink(pid_file)
        except OSError:
            pass
        if compliance.is_restart:
            logger.info("Restarting process...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        logger.info("Station shut down. 73!")


if __name__ == "__main__":
    main()
