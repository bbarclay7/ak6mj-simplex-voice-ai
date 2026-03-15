"""Ollama LLM chat with DuckDuckGo web search integration."""

import logging
import re

from ollama import chat as ollama_chat
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

# Keywords suggesting a web search would help answer the question
SEARCH_TRIGGERS = [
    "what is", "what are", "what's the", "who is", "who was",
    "when did", "when was", "when is",
    "where is", "where do", "where can",
    "how to", "how do", "how does", "how many", "how much",
    "which", "what frequency", "what band", "what mode",
    "latest", "current", "today", "news", "weather", "forecast",
    "look up", "search for", "find out", "tell me about",
    "temperature", "score", "result", "update",
    "ft8", "ft4", "wspr", "aprs", "dmr", "d-star",
    "propagation", "solar", "sunspot", "sfi", "kp index",
]


class LLM:
    def __init__(self, config: dict):
        self.model = config["llm"]["model"]
        self.max_tokens = config["llm"]["max_tokens"]
        self.temperature = config["llm"]["temperature"]
        self.search_enabled = config["search"]["enabled"]
        self.max_search_results = config["search"]["max_results"]

        self.system_prompt = config["llm"]["system_prompt"].format(
            callsign=config["callsign"]
        )
        self.messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        self.max_history = 10  # user/assistant pairs to keep

    def _needs_search(self, text: str) -> bool:
        text_lower = text.lower()
        return any(trigger in text_lower for trigger in SEARCH_TRIGGERS)

    def _web_search(self, query: str) -> str:
        try:
            results = DDGS().text(query, max_results=self.max_search_results)
            if not results:
                return ""
            return "\n".join(f"- {r['title']}: {r['body']}" for r in results)
        except Exception as e:
            logger.warning(f"Web search failed: {e}")
            return ""

    def respond(self, user_text: str, memory_context: str = "") -> str:
        """Generate a response, optionally augmented with web search results."""
        # Web search if warranted
        search_context = ""
        if self.search_enabled and self._needs_search(user_text):
            logger.info(f"Searching: {user_text}")
            search_context = self._web_search(user_text)

        if search_context:
            content = (
                f"The user asked over radio: {user_text}\n\n"
                f"Web search results:\n{search_context}\n\n"
                f"Answer concisely using these results."
            )
        else:
            content = user_text

        if memory_context:
            content = f"{memory_context}\n{content}"

        # Prepend /no_think to suppress qwen3 chain-of-thought reasoning
        content = f"/no_think\n{content}"

        self.messages.append({"role": "user", "content": content})

        # Trim history: keep system prompt + last N exchanges
        if len(self.messages) > (1 + self.max_history * 2):
            self.messages = [self.messages[0]] + self.messages[-(self.max_history * 2):]

        # Stream response
        full_response = ""
        try:
            stream = ollama_chat(
                model=self.model,
                messages=self.messages,
                stream=True,
                options={
                    "num_predict": self.max_tokens,
                    "temperature": self.temperature,
                },
            )
            for chunk in stream:
                full_response += chunk["message"]["content"]
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            full_response = "Sorry, I had trouble generating a response."

        self.messages.append({"role": "assistant", "content": full_response})
        logger.info(f"LLM raw: {full_response!r}")

        # Strip <think>...</think> blocks from reasoning models (qwen3, deepseek-r1, etc.)
        cleaned = re.sub(r"<think>.*?</think>", "", full_response, flags=re.DOTALL).strip()
        if cleaned != full_response.strip():
            logger.info(f"LLM cleaned: {cleaned!r}")

        return cleaned

    def reset(self):
        """Clear conversation history."""
        self.messages = [{"role": "system", "content": self.system_prompt}]
