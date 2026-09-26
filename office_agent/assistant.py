import os
import re
import time

from dotenv import load_dotenv

import storage

load_dotenv()


def _extract_retry_delay(message: str) -> float | None:
    """Pull the server-suggested retry delay (e.g. "retryDelay': '57s'") out of an error string."""
    match = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s", message)
    return float(match.group(1)) if match else None


CLAUDE_MODELS = [
    {"id": "claude-opus-5", "label": "Claude Opus 5", "provider": "anthropic"},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "provider": "anthropic"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "provider": "anthropic"},
]

_GEMINI_MODEL_PATTERN = re.compile(
    r"^models/gemini-(?:[\d.]+-(?:flash|pro)(?:-lite)?|(?:flash|pro)-latest)$"
)
_GEMINI_MODELS_FALLBACK = [
    {"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash", "provider": "gemini"},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "provider": "gemini"},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "provider": "gemini"},
]


def _list_gemini_models() -> list[dict]:
    """Ask the Gemini API which text-chat models this key can actually use."""
    try:
        from google import genai

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        models = []
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or []
            if "generateContent" in actions and _GEMINI_MODEL_PATTERN.match(m.name):
                models.append({
                    "id": m.name.replace("models/", ""),
                    "label": m.display_name or m.name,
                    "provider": "gemini",
                })
        if models:
            return sorted(models, key=lambda m: m["id"])
    except Exception:
        pass
    return _GEMINI_MODELS_FALLBACK


def list_available_models() -> list[dict]:
    """List every model this deployment's API keys can actually use, for a model picker."""
    models = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        models += CLAUDE_MODELS
    if os.environ.get("GEMINI_API_KEY"):
        models += _list_gemini_models()
    return models

SYSTEM_PROMPT = """You are an office assistant running on the user's own Windows PC. You help with
Word (.docx) and Excel (.xlsx) documents, PDF files (.pdf), plain text files (.txt, .csv, .md, .json,
.py, .js, .log, etc.), general questions, and writing code.

Rules:
- When the user uploads or references a file, its full path is given to you in the message. Read it with
  read_docx, read_excel, read_pdf, or read_text_file (pick the one matching its extension) before doing
  anything else with it, so you know what's actually in it.
- Always read a file with read_docx/read_excel before editing it, so your edit matches what's there.
- To make an Excel sheet look better, use format_excel_range (bold, colors, number formats) and
  create_excel_chart (bar/line/pie) once the data is in place.
- After creating or editing a Word/Excel file, open it with open_in_app so the user can see the change
  immediately, unless the user asked you not to.
- Ask for a file's full path if it's ambiguous; use list_office_files to help locate files in a folder.
- When you write code, put it in a fenced code block with the language tag (e.g. ```python), so it renders
  with a copy button in the UI. Keep prose explanations outside the code block.
- A message may start with a bracketed note like "[About the user — always keep this in mind and follow
  it]: ..." — that is a standing instruction from the user about themselves (e.g. their name, preferred
  language, tone). Always honor it for the rest of the conversation, not just that one reply.
"""


class ClaudeAssistant:
    """Office assistant backed by the Anthropic Claude API."""

    def __init__(self, model: str = "claude-opus-5"):
        import anthropic

        from anthropic_tools import ALL_TOOLS

        storage.init_db()
        self.conversation_id = storage.start_conversation()
        self.client = anthropic.Anthropic()
        self.tools = ALL_TOOLS
        self.messages = []
        self.model = model

    def send(self, user_text: str) -> str:
        import anthropic

        self.messages.append({"role": "user", "content": user_text})
        storage.save_message(self.conversation_id, "user", user_text)

        attempts = 3
        last_message = None
        for attempt in range(attempts):
            try:
                runner = self.client.beta.messages.tool_runner(
                    model=self.model,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    thinking={"type": "adaptive"},
                    output_config={"effort": "high"},
                    tools=self.tools,
                    messages=self.messages,
                )
                for message in runner:
                    last_message = message
                break
            except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError):
                if attempt < attempts - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                self.messages.pop()
                return "The AI service is unreachable right now. Please check your connection and try again."
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and attempt < attempts - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                self.messages.pop()
                return f"The AI service returned an error ({exc.status_code}). Please try again in a moment."
            except Exception as exc:
                self.messages.pop()
                return f"Something went wrong talking to the assistant: {exc}"

        if last_message is None:
            self.messages.pop()
            return "(no response)"

        self.messages.append({"role": "assistant", "content": last_message.content})
        text = "\n".join(
            block.text for block in last_message.content if block.type == "text"
        ).strip()
        storage.save_message(self.conversation_id, "assistant", text)
        return text or "(no text response)"


class GeminiAssistant:
    """Office assistant backed by Google Gemini, for testing without an Anthropic key."""

    def __init__(self, model: str | None = None):
        from google import genai
        from google.genai import types

        from tools import PLAIN_TOOLS

        storage.init_db()
        self.conversation_id = storage.start_conversation()

        self.client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=60_000),
        )
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
        self.chat = self.client.chats.create(
            model=self.model,
            config=types.GenerateContentConfig(
                tools=PLAIN_TOOLS,
                system_instruction=SYSTEM_PROMPT,
            ),
        )

    def send(self, user_text: str) -> str:
        storage.save_message(self.conversation_id, "user", user_text)
        attempts = 3
        text = "(no response)"
        for attempt in range(attempts):
            is_last = attempt == attempts - 1
            try:
                response = self.chat.send_message(user_text)
                text = (response.text or "").strip() or "(no text response)"
                break
            except Exception as exc:
                msg = str(exc)
                rate_limited = "RESOURCE_EXHAUSTED" in msg or "429" in msg
                overloaded = "503" in msg or "UNAVAILABLE" in msg
                unavailable_model = "NOT_FOUND" in msg or "no longer available" in msg
                if rate_limited and not is_last:
                    # The free Gemini tier caps requests per minute; the API tells us
                    # exactly how long to wait, so honor that instead of failing outright.
                    delay = _extract_retry_delay(msg) or 20
                    time.sleep(min(delay + 1, 60))
                    continue
                if overloaded and not is_last:
                    time.sleep(2 * (attempt + 1))
                    continue
                if unavailable_model:
                    text = (
                        f"The model '{self.model}' isn't available for this API key (Google may have "
                        "retired it for new users). Please pick a different model from the model switcher."
                    )
                elif rate_limited:
                    text = (
                        "The free Gemini plan only allows a few requests per minute, and that "
                        "limit was just hit. Please wait about a minute and send your message again."
                    )
                else:
                    text = f"Error talking to Gemini: {exc}"
                break
        storage.save_message(self.conversation_id, "assistant", text)
        return text


def get_assistant(model_id: str | None = None):
    """Pick a provider, either explicitly by model id or by whichever API key is configured."""
    load_dotenv()
    if model_id:
        if model_id.startswith("claude"):
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot use a Claude model.")
            return ClaudeAssistant(model=model_id)
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY is not set; cannot use a Gemini model.")
        return GeminiAssistant(model=model_id)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeAssistant()
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiAssistant()
    raise RuntimeError(
        "No API key found. Set ANTHROPIC_API_KEY (Claude) or GEMINI_API_KEY (Gemini) in .env."
    )


# Kept for backward compatibility with existing imports.
OfficeAssistant = ClaudeAssistant
