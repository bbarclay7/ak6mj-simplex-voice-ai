"""Speech-to-text via mlx-whisper (Apple Silicon optimized)."""

import logging
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


class STT:
    def __init__(self, config: dict):
        self.model_name = config["stt"]["model"]
        self._model_loaded = False

    def _ensure_loaded(self):
        if self._model_loaded:
            return
        import mlx_whisper  # noqa: F401  — verify importable
        self._model_loaded = True
        logger.info(f"STT model ready: {self.model_name}")

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """Transcribe a float32 numpy audio array to text."""
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

        # Normalize to [-1, 1] — radio audio can be variable level
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.95

        # mlx_whisper.transcribe expects a file path or np.ndarray at 16kHz
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
        logger.info(f"Transcription: {text!r}")
        return text
