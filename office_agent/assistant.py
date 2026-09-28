import inspect
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


OPENCODE_FREE_MODELS = [
    {"id": "big-pickle", "label": "Big Pickle (free)", "provider": "opencode"},
    {"id": "space-bunny-free", "label": "Space Bunny (free)", "provider": "opencode"},
    {"id": "longcat-2.5-preview-free", "label": "LongCat 2.5 Preview (free)", "provider": "opencode"},
    {"id": "mimo-v2.6-flash-free", "label": "MiMo V2.6 Flash (free)", "provider": "opencode"},
    {"id": "mimo-v2.5-free", "label": "MiMo V2.5 (free)", "provider": "opencode"},
    {"id": "ling-3.0-flash-fin-free", "label": "Ling 3.0 Flash Fin (free)", "provider": "opencode"},
    {"id": "nemotron-3-ultra-free", "label": "Nemotron 3 Ultra (free)", "provider": "opencode"},
    {"id": "nemotron-3.5-lightning-free", "label": "Nemotron 3.5 Lightning (free)", "provider": "opencode"},
]


def list_available_models() -> list[dict]:
    """List every model this deployment's API keys can actually use, for a model picker."""
    models = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        models += CLAUDE_MODELS
    if os.environ.get("GEMINI_API_KEY"):
        models += _list_gemini_models()
    if os.environ.get("OPENCODE_API_KEY"):
        models += OPENCODE_FREE_MODELS
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
- After creating or editing a Word/Excel file, do NOT open it automatically. The app already shows the
  user the file with a button to open it themselves. Only call open_in_app if the user explicitly asks
  you to open a file.
- Ask for a file's full path if it's ambiguous; use list_office_files to help locate files in a folder.
- When you write code, put it in a fenced code block with the language tag (e.g. ```python), so it renders
  with a copy button in the UI. Keep prose explanations outside the code block.
- A message may start with a bracketed note like "[About the user — always keep this in mind and follow
  it]: ..." — that is a standing instruction from the user about themselves (e.g. their name, preferred
  language, tone). Always honor it for the rest of the conversation, not just that one reply.
"""


class ClaudeAssistant:
    """Office assistant backed by the Anthropic Claude API."""

    def __init__(self, model: str = "claude-opus-5", user_id: int | None = None):
        import anthropic

        from anthropic_tools import ALL_TOOLS

        storage.init_db()
        self.user_id = user_id
        self.conversation_id = storage.start_conversation(user_id)
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

    def __init__(self, model: str | None = None, user_id: int | None = None):
        from google import genai
        from google.genai import types

        from tools import PLAIN_TOOLS

        storage.init_db()
        self.user_id = user_id
        self.conversation_id = storage.start_conversation(user_id)

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


def _json_schema_for(annotation) -> dict:
    """Best-effort mapping from a Python type hint to a JSON Schema fragment."""
    if annotation in (str, inspect.Parameter.empty):
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        item_args = getattr(annotation, "__args__", (str,))
        return {"type": "array", "items": _json_schema_for(item_args[0])}
    return {"type": "string"}


def _function_to_openai_tool(fn) -> dict:
    """Turn one of our plain Python tool functions into an OpenAI-style function-calling schema."""
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""
    description = doc.split("\n\n")[0].strip().replace("\n", " ")
    properties = {}
    required = []
    for name, param in sig.parameters.items():
        properties[name] = _json_schema_for(param.annotation)
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


class OpenCodeAssistant:
    """Office assistant backed by OpenCode Zen's free, OpenAI-compatible model gateway."""

    MAX_TOOL_ROUNDS = 8

    def __init__(self, model: str | None = None, user_id: int | None = None):
        from openai import OpenAI

        from tools import PLAIN_TOOLS

        storage.init_db()
        self.user_id = user_id
        self.conversation_id = storage.start_conversation(user_id)
        self.client = OpenAI(
            api_key=os.environ["OPENCODE_API_KEY"],
            base_url="https://opencode.ai/zen/v1",
        )
        self.model = model or os.environ.get("OPENCODE_MODEL", "big-pickle")
        self.tools_by_name = {fn.__name__: fn for fn in PLAIN_TOOLS}
        self.tool_schemas = [_function_to_openai_tool(fn) for fn in PLAIN_TOOLS]
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def send(self, user_text: str) -> str:
        import json as _json

        self.messages.append({"role": "user", "content": user_text})
        storage.save_message(self.conversation_id, "user", user_text)

        text = "(no response)"
        try:
            for _ in range(self.MAX_TOOL_ROUNDS):
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=self.tool_schemas,
                )
                message = response.choices[0].message
                self.messages.append(message.model_dump(exclude_none=True))
                if not message.tool_calls:
                    text = (message.content or "").strip() or "(no text response)"
                    break
                for call in message.tool_calls:
                    fn = self.tools_by_name.get(call.function.name)
                    try:
                        args = _json.loads(call.function.arguments or "{}")
                        result = fn(**args) if fn else f"Unknown tool: {call.function.name}"
                    except Exception as exc:
                        result = f"Error running {call.function.name}: {exc}"
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": str(result),
                    })
            else:
                text = "The assistant made too many tool calls in a row. Please try rephrasing your request."
        except Exception as exc:
            self.messages.pop()
            text = f"Error talking to OpenCode: {exc}"

        storage.save_message(self.conversation_id, "assistant", text)
        return text


_PROVIDER_CLASSES = {
    "anthropic": ClaudeAssistant,
    "gemini": GeminiAssistant,
    "opencode": OpenCodeAssistant,
}

_PROVIDER_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "opencode": "OPENCODE_API_KEY",
}


def get_assistant(model_id: str | None = None, user_id: int | None = None):
    """Pick a provider, either explicitly by model id or by whichever API key is configured."""
    load_dotenv()
    if model_id:
        provider = next((m["provider"] for m in list_available_models() if m["id"] == model_id), None)
        if provider is None:
            # Not in the curated list (e.g. a custom GEMINI_MODEL override) — guess by prefix.
            if model_id.startswith("claude"):
                provider = "anthropic"
            elif model_id.startswith("gemini"):
                provider = "gemini"
            else:
                raise RuntimeError(
                    f"Unknown model '{model_id}'. Make sure the matching API key "
                    "(ANTHROPIC_API_KEY, GEMINI_API_KEY, or OPENCODE_API_KEY) is set in .env."
                )
        env_key = _PROVIDER_ENV_KEYS[provider]
        if not os.environ.get(env_key):
            raise RuntimeError(f"{env_key} is not set; cannot use model '{model_id}'.")
        return _PROVIDER_CLASSES[provider](model=model_id, user_id=user_id)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeAssistant(user_id=user_id)
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiAssistant(user_id=user_id)
    if os.environ.get("OPENCODE_API_KEY"):
        return OpenCodeAssistant(user_id=user_id)
    raise RuntimeError(
        "No API key found. Set ANTHROPIC_API_KEY (Claude), GEMINI_API_KEY (Gemini), "
        "or OPENCODE_API_KEY (OpenCode Zen) in .env."
    )


# Kept for backward compatibility with existing imports.
OfficeAssistant = ClaudeAssistant
