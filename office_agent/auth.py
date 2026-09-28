import os
import secrets
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import Cookie, HTTPException

import storage

load_dotenv()

SESSION_COOKIE = "session_token"
STATE_COOKIE = "google_oauth_state"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def google_redirect_uri() -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8010/auth/google/callback")


def build_google_auth_url(state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_google_code(code: str) -> dict:
    """Trade an OAuth code for the signed-in Google account's profile info."""
    token_resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": google_redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    userinfo_resp = httpx.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    userinfo_resp.raise_for_status()
    return userinfo_resp.json()


def unique_username(base: str) -> str:
    base = "".join(c for c in base.lower() if c.isalnum()) or "user"
    candidate = base
    n = 1
    while storage.get_user_by_username(candidate) is not None:
        n += 1
        candidate = f"{base}{n}"
    return candidate


def find_or_create_google_user(userinfo: dict) -> dict:
    email = userinfo.get("email", "")
    existing = storage.get_user_by_email(email) if email else None
    if existing:
        return existing
    base_username = userinfo.get("name") or (email.split("@")[0] if email else "user")
    username = unique_username(base_username)
    return storage.create_user(username=username, password=None, email=email, auth_provider="google")


def get_current_user(session_token: str | None = Cookie(default=None)) -> dict:
    if not session_token:
        raise HTTPException(status_code=401, detail="Not signed in")
    user = storage.get_user_by_session(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired, please sign in again")
    return user


def new_state_token() -> str:
    return secrets.token_urlsafe(16)
