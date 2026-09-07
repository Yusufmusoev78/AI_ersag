import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import storage
from assistant import get_assistant

app = FastAPI(title="Office AI Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(__file__).parent / "uploads"

TEXT_EXTENSIONS = {".txt", ".csv", ".md", ".json", ".log", ".py", ".js", ".ts", ".html", ".css"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".docx", ".xlsx"}

_bot = None


def get_bot():
    global _bot
    if _bot is None:
        _bot = get_assistant()
    return _bot


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


class OperationItem(BaseModel):
    tool_name: str
    file_path: str
    detail: str
    created_at: str


class ConversationSummary(BaseModel):
    id: int
    title: str
    started_at: str


class ConversationMessage(BaseModel):
    role: str
    content: str
    created_at: str


class SessionInfo(BaseModel):
    conversation_id: int


class UploadResponse(BaseModel):
    filename: str
    path: str
    supported: bool


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    reply = get_bot().send(req.message)
    return ChatResponse(reply=reply)


@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload").name
    dest = UPLOAD_DIR / safe_name
    stem, suffix = dest.stem, dest.suffix
    counter = 1
    while dest.exists():
        dest = UPLOAD_DIR / f"{stem} ({counter}){suffix}"
        counter += 1
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    supported = dest.suffix.lower() in SUPPORTED_EXTENSIONS
    return UploadResponse(filename=dest.name, path=str(dest.resolve()), supported=supported)


@app.get("/operations", response_model=list[OperationItem])
def operations(limit: int = 20) -> list[OperationItem]:
    rows = storage.recent_operations(limit)
    return [
        OperationItem(tool_name=t, file_path=f, detail=d or "", created_at=c)
        for t, f, d, c in rows
    ]


@app.get("/session", response_model=SessionInfo)
def session() -> SessionInfo:
    return SessionInfo(conversation_id=get_bot().conversation_id)


@app.post("/conversations/new", response_model=SessionInfo)
def new_conversation() -> SessionInfo:
    global _bot
    _bot = get_assistant()
    return SessionInfo(conversation_id=_bot.conversation_id)


@app.get("/conversations", response_model=list[ConversationSummary])
def list_conversations() -> list[ConversationSummary]:
    rows = storage.list_conversations()
    result = []
    for cid, started_at, first_message in rows:
        title = (first_message or "").strip().splitlines()[0][:60] if first_message else "New chat"
        result.append(ConversationSummary(id=cid, title=title, started_at=started_at))
    return result


@app.get("/conversations/{conversation_id}/messages", response_model=list[ConversationMessage])
def conversation_messages(conversation_id: int) -> list[ConversationMessage]:
    rows = storage.get_conversation_messages(conversation_id)
    return [ConversationMessage(role=r, content=c, created_at=t) for r, c, t in rows]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
