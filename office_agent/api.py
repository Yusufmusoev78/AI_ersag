import os
import secrets
import shutil
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
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
_bot_user_id: int | None = None
_current_model: str | None = None

CurrentUser = Depends(auth.get_current_user)


def get_bot(user_id: int):
    global _bot, _bot_user_id
    if _bot is None or _bot_user_id != user_id:
        _bot = get_assistant(_current_model, user_id=user_id)
        _bot_user_id = user_id
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


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    id: int
    username: str
    email: str | None = ""
    auth_provider: str
    avatar_color: str | None = None


class AuthStatus(BaseModel):
    google_enabled: bool


class AvatarColorRequest(BaseModel):
    color: str


def _set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.get("/auth/status", response_model=AuthStatus)
def auth_status() -> AuthStatus:
    return AuthStatus(google_enabled=auth.google_configured())


@app.get("/auth/me", response_model=UserInfo)
def auth_me(user: dict = CurrentUser) -> UserInfo:
    return UserInfo(**user)


@app.post("/auth/avatar-color", response_model=UserInfo)
def set_avatar_color(req: AvatarColorRequest, user: dict = CurrentUser) -> UserInfo:
    storage.set_user_avatar_color(user["id"], req.color)
    updated = storage.get_user_by_id(user["id"])
    return UserInfo(**updated)


@app.post("/auth/register", response_model=UserInfo)
def register(req: RegisterRequest, response: Response) -> UserInfo:
    username = req.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if storage.get_user_by_username(username):
        raise HTTPException(status_code=400, detail="That username is already taken")

    user = storage.create_user(username=username, password=req.password, email=req.email.strip())
    token = storage.create_session(user["id"])
    _set_session_cookie(response, token)
    return UserInfo(**user)


@app.post("/auth/login", response_model=UserInfo)
def login(req: LoginRequest, response: Response) -> UserInfo:
    user = storage.get_user_by_username(req.username.strip())
    if not user or not user.get("password_hash") or not storage.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = storage.create_session(user["id"])
    _set_session_cookie(response, token)
    return UserInfo(**user)


@app.post("/auth/logout")
def logout(response: Response, session_token: str | None = Cookie(default=None)) -> dict:
    if session_token:
        storage.delete_session(session_token)
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@app.get("/auth/google")
def google_login():
    if not auth.google_configured():
        raise HTTPException(status_code=400, detail="Google sign-in is not configured on this server")
    state = auth.new_state_token()
    resp = RedirectResponse(auth.build_google_auth_url(state))
    resp.set_cookie(auth.STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600)
    return resp


@app.get("/auth/google/callback")
def google_callback(
    code: str = "",
    state: str = "",
    google_oauth_state: str | None = Cookie(default=None),
):
    if not code or not state or not google_oauth_state or state != google_oauth_state:
        raise HTTPException(status_code=400, detail="Invalid or expired Google sign-in attempt")
    try:
        userinfo = auth.exchange_google_code(code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google sign-in failed: {exc}")

    user = auth.find_or_create_google_user(userinfo)
    token = storage.create_session(user["id"])
    resp = RedirectResponse("/")
    _set_session_cookie(resp, token)
    resp.delete_cookie(auth.STATE_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# Chat & files
# ---------------------------------------------------------------------------


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = CurrentUser) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    watermark = storage.latest_operation_id()
    try:
        reply = get_bot(user["id"]).send(req.message)
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
def open_file(req: OpenRequest, user: dict = CurrentUser) -> dict:
    file_path = Path(req.path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    os.startfile(str(file_path))
    storage.log_operation("open_in_app", str(file_path))
    return {"opened": str(file_path)}


@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...), user: dict = CurrentUser) -> UploadResponse:
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
def operations(limit: int = 20, user: dict = CurrentUser) -> list[OperationItem]:
    rows = storage.recent_operations(limit)
    return [
        OperationItem(tool_name=t, file_path=f, detail=d or "", created_at=c)
        for t, f, d, c in rows
    ]


@app.get("/session", response_model=SessionInfo)
def session(user: dict = CurrentUser) -> SessionInfo:
    return SessionInfo(conversation_id=get_bot(user["id"]).conversation_id)


@app.post("/conversations/new", response_model=SessionInfo)
def new_conversation(user: dict = CurrentUser) -> SessionInfo:
    global _bot, _bot_user_id
    _bot = get_assistant(_current_model, user_id=user["id"])
    _bot_user_id = user["id"]
    return SessionInfo(conversation_id=_bot.conversation_id)


@app.get("/models", response_model=ModelsResponse)
def get_models(user: dict = CurrentUser) -> ModelsResponse:
    models = list_available_models()
    if _bot is not None and _bot_user_id == user["id"]:
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
def set_model(req: SetModelRequest, user: dict = CurrentUser) -> SessionInfo:
    global _bot, _bot_user_id, _current_model
    try:
        _bot = get_assistant(req.model, user_id=user["id"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _bot_user_id = user["id"]
    _current_model = req.model
    return SessionInfo(conversation_id=_bot.conversation_id)


@app.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(user: dict = CurrentUser) -> list[ConversationSummary]:
    rows = storage.list_conversations(user["id"])
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


def _require_owned_conversation(conversation_id: int, user: dict) -> None:
    owner = storage.get_conversation_owner(conversation_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if owner != user["id"]:
        raise HTTPException(status_code=403, detail="This conversation belongs to another account")


@app.get("/conversations/{conversation_id}/messages", response_model=list[ConversationMessage])
def conversation_messages(conversation_id: int, user: dict = CurrentUser) -> list[ConversationMessage]:
    _require_owned_conversation(conversation_id, user)
    rows = storage.get_conversation_messages(conversation_id)
    return [ConversationMessage(role=r, content=c, created_at=t) for r, c, t in rows]


@app.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(conversation_id: int, req: RenameRequest, user: dict = CurrentUser) -> ConversationSummary:
    _require_owned_conversation(conversation_id, user)
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title must not be empty")
    storage.rename_conversation(conversation_id, req.title)
    rows = storage.list_conversations(user["id"])
    for cid, started_at, custom_title, _ in rows:
        if cid == conversation_id:
            return ConversationSummary(id=cid, title=custom_title or req.title, started_at=started_at)
    raise HTTPException(status_code=404, detail="conversation not found")


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user: dict = CurrentUser) -> dict:
    global _bot, _bot_user_id
    _require_owned_conversation(conversation_id, user)
    storage.delete_conversation(conversation_id)
    if _bot is not None and _bot.conversation_id == conversation_id:
        # The live assistant was pointing at the conversation we just deleted;
        # drop it so the next /chat lazily starts a fresh one instead of
        # failing a FOREIGN KEY check against a row that no longer exists.
        _bot = None
        _bot_user_id = None
    return {"deleted": conversation_id}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
