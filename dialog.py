"""Multi-turn dialog framework for the AIOC bot.

Half-duplex radio serializes everything naturally, so we only ever have one
active dialog at a time. Any subsystem (message board, net check-in, QRZ
lookup, etc.) subclasses Dialog, gets instantiated with its dependencies at
construction time, and exposes two methods:

    begin(*args) -> str      first spoken prompt; activates the dialog
    process(text, calls) -> str   handle one subsequent VOX capture

DialogManager sits in main.py and routes incoming turns to the active dialog.
"""

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class Dialog(ABC):
    """Base class for a multi-turn radio interaction."""

    @property
    @abstractmethod
    def active(self) -> bool:
        """True while the dialog is still waiting for another turn."""
        ...

    @abstractmethod
    def process(self, transcription: str, heard_calls: list[str]) -> str:
        """Handle one incoming turn. Return the spoken response."""
        ...


class DialogManager:
    """Holds at most one active Dialog and routes turns to it.

    Usage in main loop:

        if dialog_manager.active:
            reply = dialog_manager.process(transcription, heard_calls)
            transmit(reply)
            continue

        # ... intent detection ...
        if some_intent:
            d = SomeDialog(dependency)
            reply = d.begin(arg1, arg2)
            dialog_manager.begin(d)
            transmit(reply)
            continue
    """

    def __init__(self):
        self._dialog: Dialog | None = None

    @property
    def active(self) -> bool:
        return self._dialog is not None and self._dialog.active

    def begin(self, dialog: Dialog) -> None:
        """Register a newly-started dialog as the active one."""
        if self._dialog and self._dialog.active:
            logger.warning(
                f"DialogManager: replacing active {type(self._dialog).__name__} "
                f"with {type(dialog).__name__}"
            )
        self._dialog = dialog

    def process(self, transcription: str, heard_calls: list[str]) -> str:
        """Route a turn to the active dialog. Clears it when done."""
        if not self.active:
            return ""
        result = self._dialog.process(transcription, heard_calls)
        if not self._dialog.active:
            logger.debug(f"DialogManager: {type(self._dialog).__name__} completed")
            self._dialog = None
        return result
