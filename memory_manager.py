"""Per-callsign persistent memory: remembers names, interests, past QSOs."""

import json
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

# Matches standard amateur radio callsigns (US + most international)
# Examples: W1AW, K6BB, VE3XYZ, G0ABC, JA1ABC, VK2AB
_CALLSIGN_RE = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z]{1,3}|[A-Z][0-9][A-Z]{2,3})\b"
)

# Common false positives to exclude (these look like callsigns but aren't)
_EXCLUDE = {"THE", "AND", "FOR", "ARE", "YOU", "NOT", "BUT", "CAN", "ALL"}

# NATO phonetic alphabet → letter/digit, plus common non-standard variants
# (old WWII Able-Baker alphabet, casual speech, etc.)
_NATO = {
    # Standard ITU/NATO
    "alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliet": "J",
    "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O",
    "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T",
    "uniform": "U", "victor": "V", "whiskey": "W", "x-ray": "X", "xray": "X",
    "yankee": "Y", "zulu": "Z",
    # Common non-standard variants
    "able": "A", "adam": "A",
    "baker": "B", "beta": "B",
    "dog": "D",
    "easy": "E",
    "fox": "F",
    "george": "G",
    "how": "H",
    "item": "I",
    "jig": "J",
    "king": "K",
    "love": "L",
    "nan": "N",
    "oboe": "O",
    "peter": "P", "papa": "P",
    "queen": "Q",
    "sugar": "S",
    "uncle": "U",
    "william": "W", "willie": "W",
    "yoke": "Y",
    "zebra": "Z",
    # Digits
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "fiver": "5", "six": "6", "sixer": "6",
    "seven": "7", "eight": "8", "nine": "9", "niner": "9",
}

# Words that suggest phonetics are being spoken — used to decide if LLM fallback
# is worth trying when the dictionary decoder found nothing
_PHONETIC_HINT_RE = re.compile(
    r"\b(?:alpha|bravo|charlie|delta|foxtrot|golf|hotel|india|juliet|kilo|lima"
    r"|mike|november|oscar|papa|quebec|romeo|sierra|tango|uniform|victor"
    r"|whiskey|x.?ray|yankee|zulu|able|baker|beta|zero|niner)\b",
    re.IGNORECASE,
)


def _decode_phonetics(text: str) -> list[str]:
    """
    Try to extract callsigns spoken in NATO phonetics from transcribed text.
    E.g. "alpha kilo six mike juliet" → "AK6MJ"
    Returns list of decoded callsigns found.
    """
    words = re.findall(r"[a-z]+|-", text.lower())
    chars = []
    results = []

    def flush():
        if len(chars) >= 4:
            candidate = "".join(chars)
            # Must match callsign shape: 1-2 letters, 1 digit, 1-3 letters
            if re.fullmatch(r"[A-Z]{1,2}[0-9][A-Z]{1,3}", candidate):
                results.append(candidate)
        chars.clear()

    for word in words:
        mapped = _NATO.get(word)
        if mapped:
            chars.append(mapped)
        else:
            flush()

    flush()
    return results


def _extract_callsigns_llm(text: str, model: str) -> list[str]:
    """Ask a local Ollama model to decode non-standard phonetics into callsigns.

    Only called when the dictionary decoder found nothing but the text looks
    like it contains spoken phonetics. Temperature 0, 20-token budget — fast.
    """
    try:
        from ollama import chat as ollama_chat
        response = ollama_chat(
            model=model,
            messages=[{
                "role": "user",
                "content": (
                    "Extract any amateur radio callsigns from this text. "
                    "Callsigns may be spoken as phonetic words "
                    "(e.g. 'Alpha Beta One Delta' = AB1D). "
                    "Reply with ONLY the callsign(s) in standard format "
                    "(like W6ABC), one per line. Reply 'none' if there are none.\n\n"
                    f"Text: {text}"
                ),
            }],
            options={"num_predict": 20, "temperature": 0},
        )
        result = response["message"]["content"].strip()
        if result.lower() == "none":
            return []
        return [cs for cs in _CALLSIGN_RE.findall(result.upper())
                if cs not in _EXCLUDE]
    except Exception as e:
        logger.debug(f"Ollama callsign extraction failed ({model}): {e}")
        return []


def find_callsigns(text: str, exclude: set[str] | None = None,
                   model: str | None = None) -> list[str]:
    """Extract amateur radio callsigns from transcribed speech.

    Handles direct callsigns (W1AW), standard and non-standard NATO phonetics
    (Whiskey One Alpha Whiskey, or Alpha Beta One Delta).
    If model is provided, falls back to a local Ollama call when the
    dictionary decoder finds nothing but phonetic words are present.
    """
    exclude = (exclude or set()) | _EXCLUDE
    found = []

    # Direct callsign regex
    for cs in _CALLSIGN_RE.findall(text.upper()):
        if cs not in exclude:
            found.append(cs)

    # Phonetic callsigns (dictionary-based)
    for cs in _decode_phonetics(text):
        if cs not in exclude and cs not in found:
            found.append(cs)

    # LLM fallback: if nothing found yet but text contains phonetic-looking words,
    # ask the local model — handles non-standard phonetics the dictionary misses
    if not found and model and _PHONETIC_HINT_RE.search(text):
        logger.debug(f"Phonetic hint found, trying LLM callsign extraction ({model})")
        for cs in _extract_callsigns_llm(text, model):
            if cs not in exclude and cs not in found:
                found.append(cs)

    return list(dict.fromkeys(found))  # dedupe, preserve order


class MemoryManager:
    def __init__(self, config: dict):
        self.own_callsign = config["callsign"].upper()
        mem_cfg = config.get("memory", {})
        self.enabled = mem_cfg.get("enabled", True)
        self.memory_dir = mem_cfg.get("dir", "callsign_memory")
        # Small fast local model for callsign/topic extraction
        self.extraction_model = (
            mem_cfg.get("extraction_model")
            or config.get("llm", {}).get("model", "qwen3:4b")
        )
        if self.enabled:
            os.makedirs(self.memory_dir, exist_ok=True)

    # --- Profile I/O ---

    def _path(self, callsign: str) -> str:
        return os.path.join(self.memory_dir, f"{callsign.upper()}.json")

    def load(self, callsign: str) -> dict | None:
        path = self._path(callsign)
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Memory load failed for {callsign}: {e}")
            return None

    def _save(self, callsign: str, profile: dict):
        try:
            with open(self._path(callsign), "w") as f:
                json.dump(profile, f, indent=2)
        except Exception as e:
            logger.warning(f"Memory save failed for {callsign}: {e}")

    # --- Context injection ---

    def get_context(self, callsigns: list[str]) -> str:
        """Return memory context string to prepend to LLM user message."""
        if not self.enabled or not callsigns:
            return ""
        parts = []
        for cs in callsigns:
            profile = self.load(cs)
            if not profile:
                continue
            name_part = f", name: {profile['name']}" if profile.get("name") else ""
            qso_part = f", {profile.get('qso_count', 1)} QSO(s)"
            last_part = f", last heard: {profile.get('last_seen', '?')}"
            header = f"- {cs}{name_part}{qso_part}{last_part}"
            notes = profile.get("notes", [])
            note_part = ("; ".join(notes[-3:])) if notes else ""
            if note_part:
                parts.append(f"{header}\n  Past topics: {note_part}")
            else:
                parts.append(header)
        if not parts:
            return ""
        return "[Station memory]\n" + "\n".join(parts) + "\n"

    # --- QSO recording (runs in background thread) ---

    def record_qso_async(self, callsigns: list[str], transcription: str, response: str):
        """Fire-and-forget: update profiles after QSO without blocking."""
        if not self.enabled or not callsigns:
            return
        t = threading.Thread(
            target=self._record_qso,
            args=(callsigns, transcription, response),
            daemon=True,
        )
        t.start()

    def _record_qso(self, callsigns: list[str], transcription: str, response: str):
        today = time.strftime("%Y-%m-%d")
        name, topic = self._extract_info(transcription, response)
        for cs in callsigns:
            profile = self.load(cs) or {
                "callsign": cs,
                "first_seen": today,
                "qso_count": 0,
                "notes": [],
            }
            profile["last_seen"] = today
            profile["qso_count"] = profile.get("qso_count", 0) + 1
            if name and not profile.get("name"):
                profile["name"] = name
            if topic:
                notes = profile.get("notes", [])
                notes.append(f"{today}: {topic}")
                profile["notes"] = notes[-10:]  # keep last 10
            self._save(cs, profile)
            logger.info(
                f"Memory: {cs} — qsos={profile['qso_count']}, "
                f"name={profile.get('name')}, topic={topic!r}"
            )

    def _extract_info(self, transcription: str, response: str) -> tuple[str | None, str | None]:
        """
        Extract operator's first name and topic from QSO text.
        First tries simple regex; falls back to a cheap Claude Haiku call.
        """
        name = self._extract_name_simple(transcription)
        topic = self._extract_topic_simple(transcription)
        if not topic:
            topic = self._extract_with_llm(transcription, response)
        return name, topic

    @staticmethod
    def _extract_name_simple(text: str) -> str | None:
        """Catch 'my name is Bob', 'I'm Alice', 'this is Mike' patterns."""
        patterns = [
            r"my name is ([A-Z][a-z]+)",
            r"\bI'?m ([A-Z][a-z]+)",
            r"this is ([A-Z][a-z]+)",
            r"name'?s ([A-Z][a-z]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                candidate = m.group(1).capitalize()
                # Sanity: not a callsign fragment or common word
                if len(candidate) > 1 and candidate.upper() not in _EXCLUDE:
                    return candidate
        return None

    @staticmethod
    def _extract_topic_simple(text: str) -> str | None:
        """Very light topic extraction for common ham topics."""
        text_l = text.lower()
        topics = []
        keywords = {
            "weather": "weather",
            "propagation": "propagation",
            "contest": "contests",
            "antenna": "antennas",
            "radio": "equipment",
            "frequency": "frequencies",
            "license": "licensing",
            "repeater": "repeaters",
            "emergency": "emergency comms",
        }
        for kw, label in keywords.items():
            if kw in text_l:
                topics.append(label)
        return ", ".join(topics) if topics else None

    def _extract_with_llm(self, transcription: str, response: str) -> str | None:
        """Use a local Ollama model for concise topic summary when heuristics miss."""
        try:
            from ollama import chat as ollama_chat
            result = ollama_chat(
                model=self.extraction_model,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Ham radio exchange. Caller said: \"{transcription}\"\n"
                        "Summarize the topic in 5 words or fewer. "
                        "Reply with only the summary, nothing else."
                    ),
                }],
                options={"num_predict": 20, "temperature": 0},
            )
            text = result["message"]["content"].strip().rstrip(".")
            return text if text else None
        except Exception as e:
            logger.debug(f"Ollama topic extraction failed ({self.extraction_model}): {e}")
            return None
