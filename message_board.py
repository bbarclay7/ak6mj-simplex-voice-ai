"""Radio message board: personal messages and general bulletins between stations.

Personal messages are stored per-destination callsign and delivered (then cleared)
the next time that callsign is heard. Bulletins are all-stations announcements that
persist until the sender (or anyone) says they're no longer current.
"""

import json
import logging
import os
import re
import time

from memory_manager import find_callsigns

logger = logging.getLogger(__name__)

# --- Intent detection patterns ---

# "leave/send/pass a message for W6ABC: text here"
_MSG_INTENT_RE = re.compile(
    r"\b(?:leave|store|save|send|relay|pass)\s+(?:a\s+)?message\s+(?:for|to)\s+(.+)",
    re.IGNORECASE,
)

# "post/broadcast/send out a bulletin: text here"
_BULLETIN_INTENT_RE = re.compile(
    r"\b(?:post|broadcast|send\s+out|put\s+out|send)\s+(?:a\s+)?bulletin\s*[:\-,]?\s*(.+)",
    re.IGNORECASE,
)

# "that bulletin/message is no longer current/useful" or "remove/delete the bulletin"
_EXPIRE_RE = re.compile(
    r"\b(?:"
    r"that\s+(?:message|bulletin)\s+is\s+(?:no\s+longer|not)\s+(?:current|useful|valid|needed|relevant)"
    r"|(?:remove|delete|cancel|clear|expire)\s+(?:the\s+)?(?:last\s+)?(?:bulletin|message\s+board\s+entry)"
    r")\b",
    re.IGNORECASE,
)

# "any bulletins?" / "read bulletins" / "check the message board"
_READ_BULLETINS_RE = re.compile(
    r"\b(?:"
    r"(?:any|read|list|check|what(?:'s|\s+are)?|are\s+there\s+(?:any\s+)?)\s+bulletins?"
    r"|check\s+(?:the\s+)?(?:message\s+board|bulletins?)"
    r"|read\s+(?:the\s+)?(?:message\s+board|bulletins?)"
    r")\b",
    re.IGNORECASE,
)


class MessageBoard:
    """Stores and retrieves personal messages and general bulletins."""

    def __init__(self, config: dict):
        cfg = config.get("message_board", {})
        self.enabled = cfg.get("enabled", True)
        self.msg_dir = cfg.get("dir", "messages")
        if self.enabled:
            os.makedirs(self.msg_dir, exist_ok=True)
        self._bulletin_path = os.path.join(self.msg_dir, "bulletins.json")

    # --- Low-level I/O ---

    def _personal_path(self, callsign: str) -> str:
        return os.path.join(self.msg_dir, f"{callsign.upper()}.json")

    def _load(self, path: str, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"MessageBoard load error ({path}): {e}")
            return default

    def _save(self, path: str, data):
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"MessageBoard save error ({path}): {e}")

    # --- Personal messages ---

    def store_personal(self, from_call: str, to_call: str, text: str):
        path = self._personal_path(to_call)
        msgs = self._load(path, [])
        msgs.append({
            "from": from_call.upper(),
            "to": to_call.upper(),
            "text": text.strip(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M"),
        })
        self._save(path, msgs)
        logger.info(f"MessageBoard: personal msg from {from_call} to {to_call}: {text!r}")

    def deliver_personal(self, callsign: str) -> list[dict]:
        """Return pending messages for callsign and delete them."""
        path = self._personal_path(callsign)
        msgs = self._load(path, [])
        if msgs:
            os.remove(path)
            logger.info(f"MessageBoard: delivered {len(msgs)} msg(s) to {callsign}")
        return msgs

    def has_personal(self, callsign: str) -> bool:
        return os.path.exists(self._personal_path(callsign))

    # --- Bulletins ---

    def store_bulletin(self, from_call: str, text: str):
        bulletins = self._load(self._bulletin_path, [])
        bulletins.append({
            "from": from_call.upper(),
            "text": text.strip(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M"),
            "active": True,
        })
        self._save(self._bulletin_path, bulletins)
        logger.info(f"MessageBoard: bulletin from {from_call}: {text!r}")

    def active_bulletins(self) -> list[dict]:
        return [b for b in self._load(self._bulletin_path, []) if b.get("active", True)]

    def expire_last_bulletin(self) -> bool:
        """Mark the most recent active bulletin inactive. Returns True if one was found."""
        bulletins = self._load(self._bulletin_path, [])
        for b in reversed(bulletins):
            if b.get("active", True):
                b["active"] = False
                self._save(self._bulletin_path, bulletins)
                logger.info("MessageBoard: expired last bulletin")
                return True
        return False

    # --- Intent parsing ---

    def parse_intent(self, transcription: str, heard_calls: list[str]) -> dict | None:
        """
        Detect message-board commands in the transcription.
        Returns a dict with 'action' key, or None if no command found.
        """
        if not self.enabled:
            return None

        from_call = heard_calls[0] if heard_calls else "Unknown"

        # Expire/remove bulletin?
        if _EXPIRE_RE.search(transcription):
            return {"action": "expire_bulletin", "from": from_call}

        # Read bulletins on demand?
        if _READ_BULLETINS_RE.search(transcription):
            return {"action": "read_bulletins", "from": from_call}

        # Bulletin post?
        m = _BULLETIN_INTENT_RE.search(transcription)
        if m:
            text = m.group(1).strip()
            if text:
                return {"action": "store_bulletin", "from": from_call, "text": text}

        # Personal message? Detect intent, then locate target callsign + text.
        m = _MSG_INTENT_RE.search(transcription)
        if m:
            rest = m.group(1)
            candidates = find_callsigns(rest)
            if candidates:
                to_call = candidates[0]
                # Message text follows a separator (colon/dash/comma) or comes after the callsign
                sep_match = re.search(r"[:\-,]\s*(.+)", rest)
                if sep_match:
                    msg_text = sep_match.group(1).strip()
                else:
                    # Strip the first callsign token(s) from rest to get message text
                    # Works for direct callsigns; phonetic tokens harder — take what remains
                    msg_text = re.sub(
                        r"^(?:[A-Z0-9]{1,3}\s+){0,6}[A-Z0-9]+\b\s*",
                        "",
                        rest,
                        flags=re.IGNORECASE,
                    ).strip().lstrip(",:-").strip()
                if msg_text:
                    return {
                        "action": "store_personal",
                        "from": from_call,
                        "to": to_call,
                        "text": msg_text,
                    }

        return None

    # --- Command execution (returns spoken-text acknowledgement) ---

    def handle_command(self, intent: dict) -> str:
        action = intent["action"]

        if action == "store_personal":
            self.store_personal(intent["from"], intent["to"], intent["text"])
            return (
                f"Message stored for {intent['to']}. "
                f"I will relay it the next time {intent['to']} is on the air."
            )

        if action == "store_bulletin":
            self.store_bulletin(intent["from"], intent["text"])
            return "Bulletin posted. It will be announced to all stations until marked no longer current."

        if action == "expire_bulletin":
            if self.expire_last_bulletin():
                return "The last bulletin has been marked inactive and will no longer be announced."
            return "There are no active bulletins to remove."

        if action == "read_bulletins":
            bulletins = self.active_bulletins()
            if not bulletins:
                return "No active bulletins at this time."
            return self._format_bulletins(bulletins)

        return ""

    # --- Relay helpers (for main loop) ---

    def personal_relay_text(self, callsigns: list[str]) -> str:
        """Deliver and format pending personal messages for a list of callsigns."""
        if not self.enabled:
            return ""
        parts = []
        for cs in callsigns:
            msgs = self.deliver_personal(cs)
            for msg in msgs:
                parts.append(f"Message for {cs} from {msg['from']}: {msg['text']}")
        return ". ".join(parts) + "." if parts else ""

    def bulletin_relay_text(self, session_seen: set, heard_calls: list[str]) -> str:
        """
        Return bulletin text for stations not yet announced to this session.
        Updates session_seen in-place with the heard_calls.
        """
        if not self.enabled:
            return ""
        bulletins = self.active_bulletins()
        if not bulletins:
            return ""
        # Only relay once per session per callsign heard
        new_calls = [cs for cs in heard_calls if cs not in session_seen]
        if not new_calls:
            return ""
        session_seen.update(new_calls)
        return self._format_bulletins(bulletins)

    @staticmethod
    def _format_bulletins(bulletins: list[dict]) -> str:
        if not bulletins:
            return ""
        count = len(bulletins)
        label = "bulletin" if count == 1 else "bulletins"
        parts = [f"{count} active {label}."]
        for b in bulletins:
            parts.append(f"From {b['from']}: {b['text']}")
        return " ".join(parts)
