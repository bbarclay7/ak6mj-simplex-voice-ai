"""Speech-to-text via mlx-whisper (Apple Silicon optimized)."""

import logging
import re
import tempfile

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# Whisper's native sample rate — resample to this before transcription
WHISPER_SR = 16000

# Primes Whisper toward ham radio vocabulary, improving NATO phonetic recognition
HAM_PROMPT = (
    "Ham radio operator. Callsign. CQ. QSL. QRM. QRN. QSO. 73. Roger. Over. "
    "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima "
    "Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey "
    "X-ray Yankee Zulu. Zero One Two Three Four Five Six Seven Eight Nine Niner."
)

# Whisper hallucinations commonly produced on static/noise
_HALLUCINATIONS = {
    ".", "..", "...", "…",
    "you", "uh", "um", "hmm", "hm", "mhm",
    "thank you", "thanks", "thank you.", "thanks.",
    "thanks for watching", "thanks for watching.",
    "like and subscribe", "please subscribe",
    "bye", "bye.", "goodbye",
    "subtitles by", "transcribed by",
    "[music]", "[applause]", "(music)", "(applause)",
}

# Repeated phrase pattern (e.g. "the the the" or "roger roger roger roger")
_REPETITION_RE = re.compile(r"\b(\w+(?:\s+\w+){0,2})\b(?:\s+\1){3,}", re.IGNORECASE)


def _is_hallucination(text: str) -> bool:
    """Return True if text looks like a Whisper noise hallucination."""
    t = text.strip().lower().rstrip(".")
    if t in _HALLUCINATIONS:
        return True
    # Pure punctuation / whitespace
    if not re.search(r"[a-z]", t):
        return True
    # Pathological repetition
    if _REPETITION_RE.search(text):
        return True
    return False


class STT:
    def __init__(self, config: dict):
        self.model_name = config["stt"]["model"]
        stt_cfg = config.get("stt", {})
        # Discard segment if avg no_speech_prob exceeds this (0–1; lower = stricter)
        self.no_speech_threshold = stt_cfg.get("no_speech_threshold", 0.6)
        # Skip Whisper entirely if audio RMS is below this (dBFS, pre-normalization)
        self.min_energy_dbfs = stt_cfg.get("min_energy_dbfs", -55.0)
        self._model_loaded = False

    def _ensure_loaded(self):
        if self._model_loaded:
            return
        import mlx_whisper  # noqa: F401  — verify importable
        self._model_loaded = True
        logger.info(f"STT model ready: {self.model_name}")

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """Transcribe a float32 numpy audio array to text.

        Returns empty string if the audio is likely noise (energy gate,
        no_speech_prob, or known hallucination pattern).
        """
        self._ensure_loaded()
        import mlx_whisper

        # Resample to 16kHz if needed (Whisper's native rate)
        if sample_rate != WHISPER_SR:
            import librosa
            audio = librosa.resample(
                audio.astype(np.float32),
                orig_sr=sample_rate,
                target_sr=WHISPER_SR,
            )

        # --- Gate 1: energy pre-check (before spending time on Whisper) ---
        rms = np.sqrt(np.mean(audio.astype(np.float64) ** 2))
        rms_dbfs = 20.0 * np.log10(rms + 1e-10)
        if rms_dbfs < self.min_energy_dbfs:
            logger.info(f"Audio too quiet ({rms_dbfs:.1f} dBFS < {self.min_energy_dbfs}), skipping STT")
            return ""

        # Normalize to [-1, 1] — radio audio can be variable level
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.95

        # --- Transcribe ---
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            sf.write(f.name, audio, WHISPER_SR)
            result = mlx_whisper.transcribe(
                f.name,
                path_or_hf_repo=self.model_name,
                language="en",
                initial_prompt=HAM_PROMPT,
                verbose=False,
            )

        text = result.get("text", "").strip()

        # --- Gate 2: no_speech_prob from Whisper segments ---
        segments = result.get("segments", [])
        if segments:
            avg_no_speech = sum(s.get("no_speech_prob", 0.0) for s in segments) / len(segments)
            if avg_no_speech > self.no_speech_threshold:
                logger.info(
                    f"Discarding (no_speech_prob={avg_no_speech:.2f}): {text!r}"
                )
                return ""

        # --- Gate 3: known hallucination patterns ---
        if _is_hallucination(text):
            logger.info(f"Discarding hallucination: {text!r}")
            return ""

        logger.info(f"Transcription: {text!r}")
        return text
