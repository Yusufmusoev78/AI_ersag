import os

from dotenv import load_dotenv

import storage

SYSTEM_PROMPT = """You are an office assistant running on the user's own Windows PC. You help with
Word (.docx) and Excel (.xlsx) documents, plain text files (.txt, .csv, .md, .json, .py, .js, .log, etc.),
general questions, and writing code.

Rules:
- When the user uploads or references a file, its full path is given to you in the message. Read it with
  read_docx, read_excel, or read_text_file (pick the one matching its extension) before doing anything else
  with it, so you know what's actually in it.
- Always read a file with read_docx/read_excel before editing it, so your edit matches what's there.
- After creating or editing a Word/Excel file, open it with open_in_app so the user can see the change
  immediately, unless the user asked you not to.
- Ask for a file's full path if it's ambiguous; use list_office_files to help locate files in a folder.
- When you write code, put it in a fenced code block with the language tag (e.g. ```python), so it renders
  with a copy button in the UI. Keep prose explanations outside the code block.
"""


class ClaudeAssistant:
    """Office assistant backed by the Anthropic Claude API."""

    def __init__(self):
        import anthropic

        from anthropic_tools import ALL_TOOLS

        storage.init_db()
        self.conversation_id = storage.start_conversation()
        self.client = anthropic.Anthropic()
        self.tools = ALL_TOOLS
        self.messages = []

    def send(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        storage.save_message(self.conversation_id, "user", user_text)

        runner = self.client.beta.messages.tool_runner(
            model="claude-opus-5",
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            tools=self.tools,
            messages=self.messages,
        )

        last_message = None
        for message in runner:
            last_message = message

        if last_message is None:
            return "(no response)"

        self.messages.append({"role": "assistant", "content": last_message.content})
        text = "\n".join(
            block.text for block in last_message.content if block.type == "text"
        ).strip()
        storage.save_message(self.conversation_id, "assistant", text)
        return text or "(no text response)"


class GeminiAssistant:
    """Office assistant backed by Google Gemini, for testing without an Anthropic key."""

    def __init__(self):
        from google import genai
        from google.genai import types

        from tools import PLAIN_TOOLS

        storage.init_db()
        self.conversation_id = storage.start_conversation()

        self.client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=60_000),
        )
        model_name = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
        self.chat = self.client.chats.create(
            model=model_name,
            config=types.GenerateContentConfig(
                tools=PLAIN_TOOLS,
                system_instruction=SYSTEM_PROMPT,
            ),
        )

    def send(self, user_text: str) -> str:
        storage.save_message(self.conversation_id, "user", user_text)
        try:
            response = self.chat.send_message(user_text)
            text = (response.text or "").strip() or "(no text response)"
        except Exception as exc:
            text = f"Error talking to Gemini: {exc}"
        storage.save_message(self.conversation_id, "assistant", text)
        return text


def get_assistant():
    """Pick a provider based on which API key is configured in the environment."""
    load_dotenv()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeAssistant()
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiAssistant()
    raise RuntimeError(
        "No API key found. Set ANTHROPIC_API_KEY (Claude) or GEMINI_API_KEY (Gemini) in .env."
    )


# Kept for backward compatibility with existing imports.
OfficeAssistant = ClaudeAssistant
