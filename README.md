# Office AI Assistant

An AI assistant that lives on your own Windows PC and works directly with your Word, Excel, PDF, and text files. Ask it to read a spreadsheet, draft a report, fix up a document, or pull numbers out of a PDF, and it edits the real files on disk — not a sandboxed copy.

It runs as a small local web app (FastAPI backend + a single-page chat UI), so everything stays on your machine: your files, your chat history, your API keys.

## Features

- **Multi-provider chat** — switch between Anthropic Claude, Google Gemini, and OpenCode Zen's free models from a model picker in the top bar, without losing your other conversations.
- **Real file tools** — the assistant can:
  - Read and write **Word** documents (`.docx`): create, append paragraphs/headings, find & replace, add tables.
  - Read and write **Excel** workbooks (`.xlsx`): create sheets, write single cells or ranges, format cells (bold, colors, number formats), and insert bar/line/pie charts.
  - Read **PDF** files (text extraction).
  - Read and write plain text files (`.txt`, `.csv`, `.md`, `.json`, `.py`, `.js`, `.log`, ...).
  - List Office files in a folder, and open a file in its default app on request.
- **Files come back as attachments, not surprises** — when the assistant creates or edits a file, it shows up in the chat as a card with the file name, path, and an **Open** button. Nothing launches automatically.
- **Accounts** — sign in with a username/password or Google, so your chat history is yours: each account only ever sees its own conversations, even on a shared PC.
- **Conversation management** — multiple chats in a sidebar, with rename, delete, and export-to-Markdown.
- **Drag-and-drop uploads** — drop a file anywhere on the page to attach it to your next message.
- **Personalization** — an avatar color and dark/light theme tied to your account, plus a standing "about you" note that the assistant actually reads and follows on every reply (e.g. "always reply in Tajik").
- **Built to not fall over** — automatic retries with backoff on rate limits and transient API errors, and clear in-chat error messages (with a retry button) instead of raw stack traces.
- **No emoji, clean UI** — every icon in the interface is a plain SVG or text label.

## Requirements

- **Windows** (the "open this file" feature uses `os.startfile`, which is Windows-only)
- **Python 3.10+**
- An API key for at least one provider:
  - [Anthropic](https://console.anthropic.com/) (`ANTHROPIC_API_KEY`) — recommended, most capable and most reliable with tool use
  - [Google AI Studio](https://aistudio.google.com/) (`GEMINI_API_KEY`) — has a free tier with tight rate limits
  - [OpenCode Zen](https://opencode.ai/auth) (`OPENCODE_API_KEY`) — free models, OpenAI-compatible

## Setup

```bash
cd office_agent
pip install -r requirements.txt
copy .env.example .env
```

Open `.env` and add whichever key(s) you have:

```env
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
OPENCODE_API_KEY=...
```

You only need one. If more than one is set, the app prefers Claude, then Gemini, then OpenCode — but you can switch providers at any time from the model picker in the UI regardless of which one is the default.

### Accounts & sign-in

The app is gated behind a sign-in screen. The first time you run it, use the **Create account** tab to pick a username and password — no email required. Each account has its own chat history.

**"Continue with Google" is optional.** To enable it:

1. Create an OAuth 2.0 Client ID (type **Web application**) at [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials).
2. Add `http://127.0.0.1:8010/auth/google/callback` to its **Authorized redirect URIs**.
3. Add the client ID and secret to `.env`:
   ```env
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   ```
4. Restart the server. The Google button appears on the sign-in screen automatically once both variables are set — if you skip this, the app works fine with username/password accounts only.

## Running it

**Web app (recommended):**

```bash
cd office_agent
python -m uvicorn api:app --host 127.0.0.1 --port 8010
```

Then open **http://127.0.0.1:8010/** in your browser.

**Command-line version:**

```bash
cd office_agent
python agent.py
```

**Desktop (Tkinter) version:**

```bash
cd office_agent
python gui.py
```

All three share the same backend logic (`assistant.py`, `tools.py`) and the same conversation history in `data/assistant.db`.

## Configuration reference

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | one of the three | Claude API key |
| `GEMINI_API_KEY` | one of the three | Gemini API key |
| `GEMINI_MODEL` | no | Override the default Gemini model (default: `gemini-3.6-flash`) |
| `OPENCODE_API_KEY` | one of the three | OpenCode Zen API key |
| `OPENCODE_MODEL` | no | Override the default OpenCode model (default: `big-pickle`) |
| `GOOGLE_CLIENT_ID` | no | Enables "Continue with Google" on the sign-in screen |
| `GOOGLE_CLIENT_SECRET` | no | Paired with `GOOGLE_CLIENT_ID` |
| `GOOGLE_REDIRECT_URI` | no | Defaults to `http://127.0.0.1:8010/auth/google/callback` |

## Project structure

```
office_agent/
├── api.py              FastAPI server: /chat, /upload, /conversations, /models, /open, /auth/*, ...
├── auth.py              Google OAuth helpers, current-user dependency
├── assistant.py         Provider logic (Claude / Gemini / OpenCode), system prompt, retries
├── tools.py              The actual file-editing functions the AI can call
├── anthropic_tools.py   Wraps tools.py for Claude's tool-calling format
├── storage.py            SQLite persistence (users, sessions, conversations, messages, file ops)
├── agent.py              Command-line chat client
├── gui.py                 Tkinter desktop chat client
├── static/index.html    The web UI (single file: HTML + CSS + JS)
├── data/                  SQLite database lives here
├── uploads/               Files you drag-and-drop or attach land here
└── requirements.txt
```

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register`, `/auth/login`, `/auth/logout` | Account sign-up / sign-in / sign-out |
| GET | `/auth/me` | Current signed-in user |
| GET | `/auth/status` | Whether Google sign-in is configured |
| GET | `/auth/google`, `/auth/google/callback` | Google OAuth flow |
| POST | `/chat` | Send a message, get a reply (+ any files the assistant touched) |
| POST | `/upload` | Upload a file to attach to your next message |
| POST | `/open` | Open a file (by path) in its default app |
| GET | `/models` | List models available given your configured API keys |
| POST | `/model` | Switch the active model (starts a fresh conversation) |
| GET/POST | `/conversations` | List / start conversations (scoped to the signed-in account) |
| PATCH/DELETE | `/conversations/{id}` | Rename / delete a conversation you own |
| GET | `/conversations/{id}/messages` | Full transcript of one conversation you own |
| GET | `/operations` | Recent file operations log |
| GET | `/health` | Health check (no login required) |

## Notes

- All chat history, accounts, and file-operation logs are stored locally in `office_agent/data/assistant.db` — nothing is sent anywhere except to whichever AI provider you've configured. Passwords are hashed (PBKDF2-SHA256), never stored in plain text.
- Sessions are plain cookies over local HTTP, which is fine for `127.0.0.1` but is **not** meant to be exposed to the internet as-is — don't port-forward this app without adding HTTPS.
- The assistant engine is single-session: if two accounts are signed in and chatting at the same moment, whichever one sends last "wins" the active conversation. This is a personal desktop tool, not a multi-tenant server.
- The free tiers of Gemini and OpenCode Zen have low rate limits; if you hit them often, consider adding an Anthropic key.
- The AI tools are `os.startfile`-based for opening files, so this app is Windows-only as written.
