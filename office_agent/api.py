import os
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import storage
from assistant import get_assistant, list_available_models

app = FastAPI(title="Office AI Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(__file__).parent / "uploads"

TEXT_EXTENSIONS = {".txt", ".csv", ".md", ".json", ".log", ".py", ".js", ".ts", ".html", ".css"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".docx", ".xlsx", ".pdf"}

_bot = None
_current_model: str | None = None


def get_bot():
    global _bot
    if _bot is None:
        _bot = get_assistant(_current_model)
    return _bot


class ChatRequest(BaseModel):
    message: str


class FileRef(BaseModel):
    path: str
    name: str
    tool: str


class ChatResponse(BaseModel):
    reply: str
    files: list[FileRef] = []


class OpenRequest(BaseModel):
    path: str


class OperationItem(BaseModel):
    tool_name: str
    file_path: str
    detail: str
    created_at: str


class ConversationSummary(BaseModel):
    id: int
    title: str
    started_at: str


class RenameRequest(BaseModel):
    title: str


class ConversationMessage(BaseModel):
    role: str
    content: str
    created_at: str


class SessionInfo(BaseModel):
    conversation_id: int


class ModelInfo(BaseModel):
    id: str
    label: str
    provider: str


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    current: str


class SetModelRequest(BaseModel):
    model: str


class UploadResponse(BaseModel):
    filename: str
    path: str
    supported: bool


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    watermark = storage.latest_operation_id()
    try:
        reply = get_bot().send(req.message)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"The assistant is unavailable right now: {exc}")

    touched: dict[str, str] = {}
    for tool_name, file_path in storage.operations_since(watermark):
        if tool_name == "open_in_app":
            continue
        touched[file_path] = tool_name  # last tool wins if a file was touched more than once
    files = [FileRef(path=p, name=Path(p).name, tool=t) for p, t in touched.items()]
    return ChatResponse(reply=reply, files=files)


@app.post("/open")
def open_file(req: OpenRequest) -> dict:
    file_path = Path(req.path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    os.startfile(str(file_path))
    storage.log_operation("open_in_app", str(file_path))
    return {"opened": str(file_path)}


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
    _bot = get_assistant(_current_model)
    return SessionInfo(conversation_id=_bot.conversation_id)


@app.get("/models", response_model=ModelsResponse)
def get_models() -> ModelsResponse:
    models = list_available_models()
    if _bot is not None:
        current = _bot.model
    elif _current_model:
        current = _current_model
    elif os.environ.get("ANTHROPIC_API_KEY"):
        current = "claude-opus-5"
    elif os.environ.get("GEMINI_API_KEY"):
        current = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
    else:
        current = models[0]["id"] if models else ""
    return ModelsResponse(models=[ModelInfo(**m) for m in models], current=current)


@app.post("/model", response_model=SessionInfo)
def set_model(req: SetModelRequest) -> SessionInfo:
    global _bot, _current_model
    try:
        _bot = get_assistant(req.model)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _current_model = req.model
    return SessionInfo(conversation_id=_bot.conversation_id)


@app.get("/conversations", response_model=list[ConversationSummary])
def list_conversations() -> list[ConversationSummary]:
    rows = storage.list_conversations()
    result = []
    for cid, started_at, custom_title, first_message in rows:
        if custom_title:
            title = custom_title
        elif first_message:
            title = first_message.strip().splitlines()[0][:60]
        else:
            title = "New chat"
        result.append(ConversationSummary(id=cid, title=title, started_at=started_at))
    return result


@app.get("/conversations/{conversation_id}/messages", response_model=list[ConversationMessage])
def conversation_messages(conversation_id: int) -> list[ConversationMessage]:
    rows = storage.get_conversation_messages(conversation_id)
    return [ConversationMessage(role=r, content=c, created_at=t) for r, c, t in rows]


@app.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(conversation_id: int, req: RenameRequest) -> ConversationSummary:
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title must not be empty")
    storage.rename_conversation(conversation_id, req.title)
    rows = storage.list_conversations()
    for cid, started_at, custom_title, _ in rows:
        if cid == conversation_id:
            return ConversationSummary(id=cid, title=custom_title or req.title, started_at=started_at)
    raise HTTPException(status_code=404, detail="conversation not found")


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int) -> dict:
    global _bot
    storage.delete_conversation(conversation_id)
    if _bot is not None and _bot.conversation_id == conversation_id:
        # The live assistant was pointing at the conversation we just deleted;
        # drop it so the next /chat lazily starts a fresh one instead of
        # failing a FOREIGN KEY check against a row that no longer exists.
        _bot = None
    return {"deleted": conversation_id}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
