"""FCC Part 97 compliance enforcement for automated amateur station."""

import re
import time
import logging

logger = logging.getLogger(__name__)

# Phonetic alphabet (ITU/NATO)
PHONETIC = {
    "A": "Alpha", "B": "Bravo", "C": "Charlie", "D": "Delta",
    "E": "Echo", "F": "Foxtrot", "G": "Golf", "H": "Hotel",
    "I": "India", "J": "Juliet", "K": "Kilo", "L": "Lima",
    "M": "Mike", "N": "November", "O": "Oscar", "P": "Papa",
    "Q": "Quebec", "R": "Romeo", "S": "Sierra", "T": "Tango",
    "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray",
    "Y": "Yankee", "Z": "Zulu",
    "0": "Zero", "1": "One", "2": "Two", "3": "Three",
    "4": "Four", "5": "Five", "6": "Six", "7": "Seven",
    "8": "Eight", "9": "Niner",
}

# Regex patterns for content we must not transmit (§97.113, §97.117)
BLOCKED_PATTERNS = [
    # Profanity / obscenity
    r"\b(fuck|shit|damn|ass|bitch|cunt|bastard|piss)\b",
    # Commercial solicitation
    r"\b(buy now|order now|use code|discount|promo code|for sale)\b",
    r"\b(visit our website|subscribe|click the link|limited offer)\b",
    # URLs / emails (shouldn't be spoken, but filter just in case)
    r"https?://\S+",
    r"\S+@\S+\.\S+",
]

BLOCKED_RE = re.compile("|".join(BLOCKED_PATTERNS), re.IGNORECASE)

# Emergency keywords — do not interfere
EMERGENCY_WORDS = ["mayday", "emergency", "break break", "pan pan"]


def phonetic_callsign(callsign: str) -> str:
    """Convert callsign to ITU phonetic alphabet. AK6MJ -> Alpha Kilo Six Mike Juliet."""
    return " ".join(PHONETIC.get(c, c) for c in callsign.upper())


# Matches bare callsigns in text (uppercase, not already phonetic)
_CALLSIGN_RE = re.compile(r"\b([A-Z]{1,2}[0-9][A-Z]{2,3})\b")

def expand_callsigns(text: str) -> str:
    """Replace bare callsigns with NATO phonetics so TTS reads them correctly.

    'Message from W6ABC' → 'Message from Whiskey Six Alpha Bravo Charlie'
    Already-expanded text (Alpha Kilo...) is unaffected — no raw callsign pattern.
    """
    return _CALLSIGN_RE.sub(lambda m: phonetic_callsign(m.group(1)), text)


class ComplianceManager:
    """Enforce FCC Part 97 rules for an automated amateur station."""

    def __init__(self, config: dict):
        self.callsign = config["callsign"]
        self.id_interval = config["id_interval_sec"]
        self.last_id_time = 0.0  # force ID on first transmission
        self._shutdown = False
        self._restart = False
        # Pre-compile control command patterns (safety-critical; tolerates commas,
        # "please", and natural speech insertions between callsign and verb)
        cs = re.escape(self.callsign)
        self._shutdown_re = re.compile(
            rf"\b{cs}\b[\s,]*(?:please[\s,]+)?(?:shut\s+down|shutdown|go\s+silent|cease\s+operations)\b",
            re.IGNORECASE,
        )
        self._restart_re = re.compile(
            rf"\b{cs}\b[\s,]*(?:please[\s,]+)?(?:restart|reboot|reload)\b",
            re.IGNORECASE,
        )

    # --- Station Identification (§97.119) ---

    def id_due(self) -> bool:
        """True if >= id_interval seconds since last station ID."""
        return (time.time() - self.last_id_time) >= self.id_interval

    def get_id_text(self) -> str:
        """Station identification announcement text."""
        return f"This is {phonetic_callsign(self.callsign)}, automated station."

    def mark_id_sent(self):
        """Record that a station ID was just transmitted."""
        self.last_id_time = time.time()
        logger.info("Station ID sent.")

    # --- Content Filtering (§97.113, §97.117) ---

    def filter_response(self, text: str) -> str:
        """Remove blocked content from LLM output."""
        filtered = BLOCKED_RE.sub("[REDACTED]", text)
        if filtered != text:
            logger.warning(f"Content filtered: {text!r} -> {filtered!r}")
        return filtered

    # --- Input Screening ---

    def should_respond(self, transcription: str) -> bool:
        """
        Decide whether to respond to an incoming transmission.
        Returns False for noise, emergency traffic, or shutdown commands.
        """
        text = transcription.strip().lower()

        if len(text) < 3:
            return False

        # Emergency traffic — stand by, do not interfere
        if any(w in text for w in EMERGENCY_WORDS):
            logger.warning(f"Emergency traffic detected: {text!r} — standing by.")
            return False

        # Shutdown command from control operator
        if self._is_shutdown_command(text):
            logger.critical("SHUTDOWN COMMAND RECEIVED")
            self._shutdown = True
            return False

        # Restart command from control operator
        if self._is_restart_command(text):
            logger.critical("RESTART COMMAND RECEIVED")
            self.request_restart()
            return False

        return True

    def _is_shutdown_command(self, text: str) -> bool:
        return bool(self._shutdown_re.search(text))

    def _is_restart_command(self, text: str) -> bool:
        return bool(self._restart_re.search(text))

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown

    @property
    def is_restart(self) -> bool:
        return self._restart

    def request_shutdown(self):
        """Programmatic shutdown (e.g., Ctrl+C)."""
        self._shutdown = True

    def request_restart(self):
        """Programmatic restart (e.g., dashboard button or SIGUSR1)."""
        self._restart = True
        self._shutdown = True
