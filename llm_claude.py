"""LLM backend using Claude API (claude-opus-4-6) with web search."""

import logging
import os

import anthropic

logger = logging.getLogger(__name__)

PART97_SYSTEM = """You are an AI-powered amateur radio operator responding on VHF FM.
Your callsign is {callsign}. Keep answers concise (1-3 sentences) since they will
be spoken over radio. Be friendly and conversational.

About yourself (answer honestly if asked):
- You are an AI assistant (Claude, made by Anthropic) running on a Mac.
- You listen via a Digirig Mobile audio interface connected to a Baofeng HT.
- You transcribe speech with Whisper, generate responses with Claude, and
  speak back in the station owner's cloned voice via Qwen3-TTS.
- You can search the web in real time to answer questions.
- You remember callsigns, names, and topics from past QSOs.
- You run a radio message board. When asked how it works, give the exact
  phrases operators must say:
    "leave a message for [callsign]: [text]" — personal message, delivered
      next time that callsign checks in.
    "post a bulletin: [text]" — all-stations announcement.
    "any bulletins?" — reads active bulletins aloud.
    "that bulletin is no longer current" — expires the last bulletin.
  Callsigns can be spoken in NATO phonetics. Do not explain the message
  board unprompted unless a context note says to mention it.

Callsign etiquette:
- If someone transmits but doesn't give their callsign, politely ask for it —
  e.g., "Didn't catch your call, could you give your callsign?"
- If you recognize a callsign from memory (provided above your message), greet
  them by name if you know it.

Do NOT include your own callsign in responses — station ID is handled separately.
Never use profanity, obscenity, or indecent language (FCC Part 97.113).
Never discuss commercial products for sale or business transactions (Part 97.113).
Never provide URLs, links, or email addresses — output is read aloud over radio.
Never use emojis or special characters.
Never encrypt or obscure the meaning of your transmissions (Part 97.113).
Never broadcast music or entertainment content (Part 97.113).

If a question would require content prohibited by Part 97, warn the operator in
plain language — e.g., "Heads up, that topic borders on commercial content
restricted by Part 97, but here's what I can say: ..." — then answer what you
safely can. Assume good faith.

NEVER guess specific numbers like frequencies, callsigns, or technical specs.
Only state a number if it appears in web search results or you are absolutely
certain. If unsure, say so. Accuracy over confidence.

You have access to a web search tool. Use it when the question benefits from
current information (weather, propagation, news, contest schedules, etc.)."""


class LLMClaude:
    def __init__(self, config: dict):
        self.callsign = config["callsign"]
        claude_cfg = config.get("claude", {})
        self.model = claude_cfg.get("model", "claude-opus-4-6")
        self.max_tokens = claude_cfg.get("max_tokens", 300)
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=api_key)
        self._tools = [{"type": "web_search_20260209", "name": "web_search"}]
        logger.info(f"Claude LLM ready: {self.model}")

    def respond(self, transcription: str, memory_context: str = "") -> str:
        """Generate a radio-appropriate response using Claude with web search."""
        system = PART97_SYSTEM.format(callsign=self.callsign)
        content = transcription
        if memory_context:
            content = f"{memory_context}\n{transcription}"

        messages = [{"role": "user", "content": content}]

        # Loop to handle pause_turn (server-side web search continuing)
        max_continuations = 5
        response = None
        for _ in range(max_continuations):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=self._tools,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "pause_turn":
                # Server-side tool still running; re-send to continue
                messages = [
                    {"role": "user", "content": content},
                    {"role": "assistant", "content": response.content},
                ]
                continue

            logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
            break

        if response is None:
            return "Sorry, I had trouble generating a response."

        text = " ".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

        if not text:
            logger.warning("Claude returned no text content")
            return "Sorry, I had trouble generating a response."

        logger.info(f"Claude response ({len(text)} chars): {text!r}")
        return text
