#!/usr/bin/env python3
"""
Worksection MCP Web Server — full API coverage
Multi-user OAuth: each user authenticates and gets a personal mcp_token.
"""

import os
import json
import uuid
import base64
import hashlib
import secrets
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Optional, Any
from urllib.parse import urlencode, quote
from datetime import datetime, timedelta, date

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

# ── Config ────────────────────────────────────────────────────────────────────
WORKSECTION_DOMAIN  = os.getenv("WORKSECTION_DOMAIN", "")
WORKSECTION_API_KEY = os.getenv("WORKSECTION_API_KEY", "")
OAUTH_CLIENT_ID     = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI  = os.getenv("OAUTH_REDIRECT_URI",
    "https://worksection.integrateflowsystems.com/oauth/callback")
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_hex(32))

TOKENS_FILE = Path("/app/tokens.json")

# ── Per-request user context ──────────────────────────────────────────────────
_current_user: ContextVar[Optional[dict]] = ContextVar("current_user", default=None)


# ── Token storage ─────────────────────────────────────────────────────────────
def load_tokens() -> dict:
    if TOKENS_FILE.exists():
        try:
            return json.loads(TOKENS_FILE.read_text())
        except Exception:
            pass
    return {}


def save_tokens(tokens: dict) -> None:
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


# ── API helpers ───────────────────────────────────────────────────────────────
def _md5(query_params: str, api_key: str) -> str:
    """Hash is computed on raw (non-URL-encoded) query string."""
    return hashlib.md5(f"{query_params}{api_key}".encode()).hexdigest()


def _admin_url(action: str, api_key: str, domain: str, **params) -> str:
    """
    Build Admin API URL.
    Hash on raw values; URL percent-encoded so special chars (&, —, \\n) are safe.
    """
    raw_parts = [f"action={action}"]
    for k, v in params.items():
        if v is not None:
            raw_parts.append(f"{k}={v}")
    hash_val = _md5("&".join(raw_parts), api_key)

    enc: list[tuple[str, str]] = [("action", action)]
    for k, v in params.items():
        if v is not None:
            enc.append((k, str(v)))
    enc.append(("hash", hash_val))

    return f"https://{domain}/api/admin/v2/?{urlencode(enc)}"


async def _admin(
    action: str, api_key: str, domain: str,
    method: str = "GET", files: Optional[dict] = None, **params,
) -> dict[str, Any]:
    url = _admin_url(action, api_key, domain, **params)
    async with httpx.AsyncClient(timeout=30.0) as c:
        if method == "GET":
            r = await c.get(url)
        elif files:
            r = await c.post(url, files=files)
        else:
            r = await c.post(url)
        r.raise_for_status()
        return r.json()


async def _oauth(
    action: str, access_token: str, account_url: str,
    method: str = "GET", files: Optional[dict] = None, **params,
) -> dict[str, Any]:
    """
    OAuth API requests.
    GET  → all params in URL query string (properly encoded).
    POST → action in URL, other params in form body (handles long text correctly).
    POST + files → multipart with params as form fields.
    """
    base = f"{account_url}/api/oauth2"
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=60.0) as c:
        if method == "GET":
            query: dict[str, str] = {"action": action}
            for k, v in params.items():
                if v is not None:
                    query[k] = str(v)
            r = await c.get(f"{base}?{urlencode(query)}", headers=headers)
        elif files:
            url = f"{base}?action={quote(action)}"
            data = {k: str(v) for k, v in params.items() if v is not None}
            r = await c.post(url, headers=headers, files=files, data=data)
        else:
            url = f"{base}?action={quote(action)}"
            data = {k: str(v) for k, v in params.items() if v is not None}
            r = await c.post(url, data=data, headers=headers)

        r.raise_for_status()
        return r.json()


async def _refresh_access_token(user: dict) -> Optional[str]:
    """Attempt to refresh OAuth access token. Returns new access token or None."""
    refresh_token = user.get("refresh_token")
    if not refresh_token or not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://worksection.com/oauth2/token",
                data={
                    "client_id":     OAUTH_CLIENT_ID,
                    "client_secret": OAUTH_CLIENT_SECRET,
                    "grant_type":    "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            if r.status_code != 200:
                return None
            return r.json().get("access_token")
    except Exception:
        return None


def _get_creds() -> dict:
    user = _current_user.get()
    if user:
        return {"type": "oauth", "token": user["access_token"],
                "url": user["account_url"], "user": user}
    if WORKSECTION_API_KEY and WORKSECTION_DOMAIN:
        return {"type": "admin"}
    raise ValueError("No credentials: authenticate via OAuth or set WORKSECTION_API_KEY")


async def _call(
    action: str, method: str = "GET",
    files: Optional[dict] = None, **params,
) -> dict[str, Any]:
    creds = _get_creds()
    if creds["type"] == "oauth":
        try:
            return await _oauth(action, creds["token"], creds["url"],
                                method, files, **params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                # Token expired — try refresh
                new_token = await _refresh_access_token(creds["user"])
                if new_token:
                    mcp_token = creds["user"].get("mcp_token")
                    if mcp_token:
                        tokens = load_tokens()
                        if mcp_token in tokens:
                            tokens[mcp_token]["access_token"] = new_token
                            save_tokens(tokens)
                    return await _oauth(action, new_token, creds["url"],
                                        method, files, **params)
            raise
    return await _admin(action, WORKSECTION_API_KEY, WORKSECTION_DOMAIN,
                        method, files, **params)


def _fmt_date(date_str: Optional[str]) -> Optional[str]:
    """Convert YYYY-MM-DD → DD.MM.YYYY. Passes through DD.MM.YYYY or None."""
    if not date_str:
        return None
    if len(date_str) == 10 and date_str[4] == "-":
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            pass
    return date_str


def _parse_ws_date(date_str: str) -> Optional[date]:
    """Parse Worksection DD.MM.YYYY string to Python date."""
    try:
        return datetime.strptime(date_str, "%d.%m.%Y").date()
    except (ValueError, TypeError):
        return None


def _week_range() -> tuple[str, str]:
    """Return (monday, today) in DD.MM.YYYY for the current ISO week."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%d.%m.%Y"), today.strftime("%d.%m.%Y")


# ── MCP setup ─────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "worksection",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        allowed_hosts=["worksection.integrateflowsystems.com", "localhost", "127.0.0.1"],
        allowed_origins=["https://worksection.integrateflowsystems.com"],
    ),
)
_mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Worksection MCP Server", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
            tokens = load_tokens()
            if token in tokens:
                user_data = dict(tokens[token])
                user_data["mcp_token"] = token  # retained for token-refresh storage update
                _current_user.set(user_data)
        return await call_next(request)


app.add_middleware(BearerAuthMiddleware)


# ── Web routes ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    with open("/app/index.html") as f:
        return f.read()


@app.get("/oauth/authorize")
async def oauth_authorize(request: Request):
    if not OAUTH_CLIENT_ID:
        raise HTTPException(status_code=500, detail="OAuth not configured")
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    params = {
        "client_id":     OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  OAUTH_REDIRECT_URI,
        "state":         state,
        "scope": (
            "projects_read,projects_write,"
            "tasks_read,tasks_write,"
            "costs_read,costs_write,"
            "comments_read,comments_write,"
            "files_read,files_write,"
            "users_read,"
            "tags_read,tags_write"
        ),
    }
    return RedirectResponse(f"https://worksection.com/oauth2/authorize?{urlencode(params)}")


@app.get("/oauth/callback")
async def oauth_callback(request: Request, code: str, state: str):
    stored_state = request.session.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid state")

    async with httpx.AsyncClient() as c:
        r = await c.post(
            "https://worksection.com/oauth2/token",
            data={
                "client_id":     OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  OAUTH_REDIRECT_URI,
            },
        )
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to get access token")
        ws_tokens = r.json()

    mcp_token = str(uuid.uuid4())
    tokens = load_tokens()
    tokens[mcp_token] = {
        "access_token":  ws_tokens["access_token"],
        "refresh_token": ws_tokens.get("refresh_token", ""),
        "account_url":   ws_tokens["account_url"],
    }
    save_tokens(tokens)

    mcp_url = "https://worksection.integrateflowsystems.com/mcp/"
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8"/>
  <title>Авторизация успешна</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0a0c14;color:#e8eaf0;font-family:'Inter',sans-serif;
         min-height:100vh;display:flex;align-items:center;justify-content:center}}
    .card{{background:#12151f;border:1px solid rgba(255,255,255,.08);border-radius:16px;
           padding:48px 56px;max-width:600px;width:90%;text-align:center}}
    h1{{font-size:22px;font-weight:700;margin-bottom:8px}}
    .sub{{color:#6b7280;font-size:14px;margin-bottom:32px}}
    .label{{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
            color:#6b7280;margin-bottom:6px;text-align:left}}
    .box{{background:#0e1018;border:1px solid rgba(255,255,255,.08);border-radius:10px;
          padding:14px 16px;margin-bottom:20px;text-align:left;position:relative}}
    .box code{{font-family:monospace;font-size:13px;color:#93c5fd;word-break:break-all}}
    .copy-btn{{position:absolute;right:10px;top:50%;transform:translateY(-50%);
               background:#1e2130;border:1px solid rgba(255,255,255,.1);color:#9ca3af;
               border-radius:6px;padding:4px 10px;font-size:11px;cursor:pointer}}
    .warn{{background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);
           border-radius:8px;padding:12px 16px;font-size:12px;color:#fcd34d;margin-top:20px}}
  </style>
</head>
<body>
<div class="card">
  <h1>✅ Авторизация успешна</h1>
  <p class="sub">Ваш персональный токен для MCP сервера</p>
  <div class="label">Настройка Claude Desktop (claude_desktop_config.json)</div>
  <div class="box">
    <code><pre id="cfg">{{
  "mcpServers": {{
    "worksection": {{
      "command": "npx",
      "args": [
        "mcp-remote",
        "{mcp_url}",
        "--header",
        "Authorization: Bearer {mcp_token}"
      ]
    }}
  }}
}}</pre></code>
    <button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('cfg').textContent);this.textContent='✓'" style="top:16px;transform:none">Копировать</button>
  </div>
  <div class="warn">⚠️ Сохраните токен — он не будет показан повторно. Если потеряете, пройдите авторизацию заново.</div>
</div>
</body>
</html>""")


@app.post("/oauth/revoke")
async def revoke_token(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = auth[7:].strip()
    tokens = load_tokens()
    if token in tokens:
        del tokens[token]
        save_tokens(tokens)
    return {"status": "revoked"}


# ══════════════════════════════════════════════════════════════════════════════
# MCP Tools
# ══════════════════════════════════════════════════════════════════════════════

# ── Projects ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_projects(page: Optional[int] = None) -> dict[str, Any]:
    """Get list of all projects."""
    p: dict = {}
    if page: p["page"] = page
    return await _call("get_projects", **p)


@mcp.tool()
async def get_project(project_id: str) -> dict[str, Any]:
    """Get detailed information about a specific project."""
    return await _call("get_project", id_project=project_id)


@mcp.tool()
async def post_project(
    title: str,
    description: Optional[str] = None,
    email_manager: Optional[str] = None,
    datestart: Optional[str] = None,
    dateend: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a new project.
    datestart/dateend: YYYY-MM-DD
    email_manager: email of the project manager
    """
    p: dict = {"title": title}
    if description:   p["description"] = description
    if email_manager: p["email_manager"] = email_manager
    if datestart:     p["datestart"] = _fmt_date(datestart)
    if dateend:       p["dateend"] = _fmt_date(dateend)
    return await _call("post_project", method="POST", **p)


@mcp.tool()
async def update_project(
    project_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    email_manager: Optional[str] = None,
    datestart: Optional[str] = None,
    dateend: Optional[str] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    """
    Update a project.
    status: active | done | template
    datestart/dateend: YYYY-MM-DD
    """
    p: dict = {"id_project": project_id}
    if title:         p["title"] = title
    if description:   p["description"] = description
    if email_manager: p["email_manager"] = email_manager
    if datestart:     p["datestart"] = _fmt_date(datestart)
    if dateend:       p["dateend"] = _fmt_date(dateend)
    if status:        p["status"] = status
    return await _call("update_project", method="POST", **p)


# ── Tasks ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_tasks(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    page: Optional[int] = None,
    extra: Optional[str] = None,
) -> dict[str, Any]:
    """
    Get list of tasks.
    status: active | done | all
    extra: files | subtasks | comments (comma-separated)
    """
    p: dict = {}
    if project_id: p["id_project"] = project_id
    if status:     p["status"] = status
    if page:       p["page"] = page
    if extra:      p["extra"] = extra
    return await _call("get_tasks", **p)


@mcp.tool()
async def get_all_tasks(
    status: Optional[str] = None,
    extra: Optional[str] = None,
) -> dict[str, Any]:
    """Get all tasks across all projects. status: active | done | all"""
    p: dict = {}
    if status: p["status"] = status
    if extra:  p["extra"] = extra
    return await _call("get_all_tasks", **p)


@mcp.tool()
async def get_task(task_id: str, extra: Optional[str] = None) -> dict[str, Any]:
    """
    Get detailed information about a task.
    extra: files | subtasks | comments (comma-separated)
    """
    p: dict = {"id_task": task_id}
    if extra: p["extra"] = extra
    return await _call("get_task", **p)


@mcp.tool()
async def post_task(
    project_id: str,
    title: str,
    text: Optional[str] = None,
    email_user_to: Optional[str] = None,
    email_user_from: Optional[str] = None,
    priority: Optional[str] = None,
    datestart: Optional[str] = None,
    dateend: Optional[str] = None,
    max_time: Optional[str] = None,
    tags: Optional[str] = None,
    hidden: Optional[int] = None,
) -> dict[str, Any]:
    """
    Create a new task.
    priority: low | normal | high | urgent
    datestart/dateend: YYYY-MM-DD
    max_time: planned hours (e.g. '8' or '8.5')
    tags: comma-separated tag names
    hidden: 1 = private task
    """
    p: dict = {"id_project": project_id, "title": title}
    if text:               p["text"] = text
    if email_user_to:      p["email_user_to"] = email_user_to
    if email_user_from:    p["email_user_from"] = email_user_from
    if priority:           p["priority"] = priority
    if datestart:          p["datestart"] = _fmt_date(datestart)
    if dateend:            p["dateend"] = _fmt_date(dateend)
    if max_time:           p["max_time"] = max_time
    if tags:               p["tags"] = tags
    if hidden is not None: p["hidden"] = hidden
    return await _call("post_task", method="POST", **p)


@mcp.tool()
async def update_task(
    task_id: str,
    title: Optional[str] = None,
    text: Optional[str] = None,
    email_user_to: Optional[str] = None,
    priority: Optional[str] = None,
    datestart: Optional[str] = None,
    dateend: Optional[str] = None,
    max_time: Optional[str] = None,
    tags: Optional[str] = None,
) -> dict[str, Any]:
    """
    Update an existing task.
    priority: low | normal | high | urgent
    datestart/dateend: YYYY-MM-DD
    tags: comma-separated (replaces existing tags)
    """
    p: dict = {"id_task": task_id}
    if title:         p["title"] = title
    if text:          p["text"] = text
    if email_user_to: p["email_user_to"] = email_user_to
    if priority:      p["priority"] = priority
    if datestart:     p["datestart"] = _fmt_date(datestart)
    if dateend:       p["dateend"] = _fmt_date(dateend)
    if max_time:      p["max_time"] = max_time
    if tags:          p["tags"] = tags
    return await _call("update_task", method="POST", **p)


@mcp.tool()
async def assign_task(task_id: str, email_user_to: str) -> dict[str, Any]:
    """Assign a task to a user by their email."""
    return await _call("update_task", method="POST",
                       id_task=task_id, email_user_to=email_user_to)


@mcp.tool()
async def complete_task(task_id: str) -> dict[str, Any]:
    """Mark a task as completed."""
    return await _call("complete_task", method="POST", id_task=task_id)


@mcp.tool()
async def reopen_task(task_id: str) -> dict[str, Any]:
    """Reopen a completed task."""
    return await _call("reopen_task", method="POST", id_task=task_id)


@mcp.tool()
async def delete_task(task_id: str) -> dict[str, Any]:
    """Permanently delete a task."""
    return await _call("delete_task", method="POST", id_task=task_id)


@mcp.tool()
async def search_tasks(
    query: str,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    page: Optional[int] = None,
) -> dict[str, Any]:
    """Search tasks by text. status: active | done | all"""
    p: dict = {"filter": query}
    if project_id: p["id_project"] = project_id
    if status:     p["status"] = status
    if page:       p["page"] = page
    return await _call("search_tasks", **p)


# ── Subtasks ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_subtasks(task_id: str) -> dict[str, Any]:
    """Get subtasks of a task."""
    return await _call("get_task", id_task=task_id, extra="subtasks")


@mcp.tool()
async def post_subtask(
    project_id: str,
    parent_task_id: str,
    title: str,
    text: Optional[str] = None,
    email_user_to: Optional[str] = None,
    priority: Optional[str] = None,
    datestart: Optional[str] = None,
    dateend: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a subtask inside a parent task.
    priority: low | normal | high | urgent
    datestart/dateend: YYYY-MM-DD
    """
    p: dict = {"id_project": project_id, "id_parent": parent_task_id, "title": title}
    if text:          p["text"] = text
    if email_user_to: p["email_user_to"] = email_user_to
    if priority:      p["priority"] = priority
    if datestart:     p["datestart"] = _fmt_date(datestart)
    if dateend:       p["dateend"] = _fmt_date(dateend)
    return await _call("post_task", method="POST", **p)


# ── Comments ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_comments(task_id: str, include_files: bool = False) -> dict[str, Any]:
    """Get comments for a task. include_files=True also returns file attachments."""
    p: dict = {"id_task": task_id}
    if include_files: p["extra"] = "files"
    return await _call("get_comments", **p)


@mcp.tool()
async def post_comment(
    task_id: str,
    text: str,
    email_user_from: Optional[str] = None,
    hidden: Optional[int] = None,
) -> dict[str, Any]:
    """
    Add a comment to a task.
    hidden: 1 = private (visible to team members only)
    """
    p: dict = {"id_task": task_id, "text": text}
    if email_user_from:    p["email_user_from"] = email_user_from
    if hidden is not None: p["hidden"] = hidden
    return await _call("post_comment", method="POST", **p)


@mcp.tool()
async def update_comment(task_id: str, comment_id: str, text: str) -> dict[str, Any]:
    """Edit the text of an existing comment."""
    return await _call("update_comment", method="POST",
                       id_task=task_id, id_comment=comment_id, text=text)


@mcp.tool()
async def delete_comment(task_id: str, comment_id: str) -> dict[str, Any]:
    """Delete a comment."""
    return await _call("delete_comment", method="POST",
                       id_task=task_id, id_comment=comment_id)


# ── Costs / Time ──────────────────────────────────────────────────────────────

@mcp.tool()
async def get_costs(
    task_id: Optional[str] = None,
    project_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: Optional[int] = None,
) -> dict[str, Any]:
    """
    Get time/cost entries for a task or project.
    date_from / date_to: YYYY-MM-DD or DD.MM.YYYY
    """
    p: dict = {}
    if task_id:    p["id_task"] = task_id
    if project_id: p["id_project"] = project_id
    if date_from:  p["date_from"] = _fmt_date(date_from)
    if date_to:    p["date_to"] = _fmt_date(date_to)
    if page:       p["page"] = page
    return await _call("get_costs", **p)


@mcp.tool()
async def add_costs(
    task_id: str,
    time: Optional[str] = None,
    money: Optional[str] = None,
    date: Optional[str] = None,
    comment: Optional[str] = None,
    email_user_from: Optional[str] = None,
) -> dict[str, Any]:
    """
    Log time or money cost on a task.
    time: decimal hours or HH:MM — e.g. '2.5' or '2:30'
    money: decimal amount — e.g. '150.00'
    date: YYYY-MM-DD (auto-converted to DD.MM.YYYY)
    At least one of time or money is required.
    """
    if not time and not money:
        raise ValueError("Provide at least one of: time, money")
    p: dict = {"id_task": task_id}
    if time:            p["time"] = time
    if money:           p["money"] = money
    if date:            p["date"] = _fmt_date(date)
    if comment:         p["comment"] = comment
    if email_user_from: p["email_user_from"] = email_user_from
    return await _call("add_costs", **p)


@mcp.tool()
async def update_costs(
    cost_id: str,
    time: Optional[str] = None,
    money: Optional[str] = None,
    date: Optional[str] = None,
    comment: Optional[str] = None,
) -> dict[str, Any]:
    """Update an existing time/cost entry. date: YYYY-MM-DD"""
    p: dict = {"id_cost": cost_id}
    if time:    p["time"] = time
    if money:   p["money"] = money
    if date:    p["date"] = _fmt_date(date)
    if comment: p["comment"] = comment
    return await _call("update_costs", **p)


@mcp.tool()
async def delete_costs(cost_id: str) -> dict[str, Any]:
    """Delete a time/cost entry by its ID."""
    return await _call("delete_costs", id_cost=cost_id)


# ── Timers (OAuth only) ───────────────────────────────────────────────────────

@mcp.tool()
async def get_my_timer() -> dict[str, Any]:
    """Get the currently running timer for the authenticated user (OAuth only)."""
    return await _call("get_my_timer")


@mcp.tool()
async def start_my_timer(task_id: str) -> dict[str, Any]:
    """Start a timer on a task for the authenticated user (OAuth only)."""
    return await _call("start_my_timer", id_task=task_id)


@mcp.tool()
async def stop_my_timer(comment: Optional[str] = None) -> dict[str, Any]:
    """Stop the running timer and optionally add a comment (OAuth only)."""
    p: dict = {}
    if comment: p["comment"] = comment
    return await _call("stop_my_timer", **p)


@mcp.tool()
async def get_timers() -> dict[str, Any]:
    """Get all active timers across the account."""
    return await _call("get_timers")


# ── Files ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_files(
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    """Get files attached to a project or task. At least one ID is required."""
    if not project_id and not task_id:
        raise ValueError("Provide project_id or task_id")
    p: dict = {}
    if project_id: p["id_project"] = project_id
    if task_id:    p["id_task"] = task_id
    return await _call("get_files", **p)


@mcp.tool()
async def upload_file(
    task_id: str,
    file_name: str,
    file_content_base64: str,
    comment: Optional[str] = None,
    comment_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Upload and attach a file to a task (or to a specific comment).
    file_name: original filename with extension, e.g. 'report.pdf'
    file_content_base64: file bytes encoded as base64
    comment: optional text comment to accompany the file
    comment_id: attach to a specific comment instead of the task root
    """
    try:
        file_bytes = base64.b64decode(file_content_base64)
    except Exception as exc:
        raise ValueError(f"Invalid base64 content: {exc}") from exc

    p: dict = {"id_task": task_id}
    if comment:    p["comment"] = comment
    if comment_id: p["id_comment"] = comment_id

    return await _call("upload_file", method="POST",
                       files={"file": (file_name, file_bytes)}, **p)


# ── Users ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_users(page: Optional[int] = None) -> dict[str, Any]:
    """Get list of all users in the account."""
    p: dict = {}
    if page: p["page"] = page
    return await _call("get_users", **p)


@mcp.tool()
async def get_user_groups() -> dict[str, Any]:
    """Get list of user teams/groups in the account."""
    return await _call("get_user_groups")


@mcp.tool()
async def get_project_members(project_id: str) -> dict[str, Any]:
    """Get members (participants) of a specific project."""
    return await _call("get_project_users", id_project=project_id)


@mcp.tool()
async def me() -> dict[str, Any]:
    """Get current authenticated user info (OAuth only)."""
    return await _call("me")


@mcp.tool()
async def subscribe_user(task_id: str, email_user: str) -> dict[str, Any]:
    """Subscribe a user to task notifications."""
    return await _call("subscribe", id_task=task_id, email_user=email_user)


@mcp.tool()
async def unsubscribe_user(task_id: str, email_user: str) -> dict[str, Any]:
    """Unsubscribe a user from task notifications."""
    return await _call("unsubscribe", id_task=task_id, email_user=email_user)


# ── Tags ──────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_tags() -> dict[str, Any]:
    """Get list of all legacy tags in the account."""
    return await _call("get_tags")


@mcp.tool()
async def get_task_tag_groups(
    project_id: Optional[str] = None,
    type: Optional[str] = None,
) -> dict[str, Any]:
    """
    Get task tag groups (label sets).
    type: status | label
    """
    p: dict = {}
    if project_id: p["id_project"] = project_id
    if type:       p["type"] = type
    return await _call("get_task_tag_groups", **p)


@mcp.tool()
async def get_task_tags(
    task_id: Optional[str] = None,
    project_id: Optional[str] = None,
    group: Optional[str] = None,
) -> dict[str, Any]:
    """Get tags assigned to a task or all tasks in a project."""
    p: dict = {}
    if task_id:    p["id_task"] = task_id
    if project_id: p["id_project"] = project_id
    if group:      p["group"] = group
    return await _call("get_task_tags", **p)


@mcp.tool()
async def update_task_tags(
    task_id: str,
    group: str,
    add_tags: Optional[str] = None,
    remove_tags: Optional[str] = None,
) -> dict[str, Any]:
    """
    Add or remove tags on a task within a specific tag group.
    add_tags / remove_tags: comma-separated tag names or IDs
    """
    p: dict = {"id_task": task_id, "group": group}
    if add_tags:    p["plus"] = add_tags
    if remove_tags: p["minus"] = remove_tags
    return await _call("update_task_tags", **p)


# ── Account ───────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_account_info() -> dict[str, Any]:
    """Get information about the Worksection account (limits, settings, plan)."""
    return await _call("get_account")


# ══════════════════════════════════════════════════════════════════════════════
# Compound tools  (multiple API calls combined into one for efficiency)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_task_full(task_id: str) -> dict[str, Any]:
    """
    Get a task with ALL related data in a single round-trip:
    subtasks, comments, files, and time/cost entries.
    """
    task_data, costs_data = await asyncio.gather(
        _call("get_task", id_task=task_id, extra="subtasks,comments,files"),
        _call("get_costs", id_task=task_id),
        return_exceptions=True,
    )
    result: dict[str, Any] = {}
    if isinstance(task_data, Exception):
        result["task_error"] = str(task_data)
    else:
        result.update(task_data)
    result["costs"] = costs_data if not isinstance(costs_data, Exception) \
                      else {"error": str(costs_data)}
    return result


@mcp.tool()
async def get_project_summary(project_id: str) -> dict[str, Any]:
    """
    Full project overview: details, active tasks, members, and tag groups —
    all in one call.
    """
    project, tasks, members, tag_groups = await asyncio.gather(
        _call("get_project", id_project=project_id),
        _call("get_tasks", id_project=project_id, status="active"),
        _call("get_project_users", id_project=project_id),
        _call("get_task_tag_groups", id_project=project_id),
        return_exceptions=True,
    )
    return {
        "project":    project    if not isinstance(project, Exception)    else {"error": str(project)},
        "tasks":      tasks      if not isinstance(tasks, Exception)      else {"error": str(tasks)},
        "members":    members    if not isinstance(members, Exception)    else {"error": str(members)},
        "tag_groups": tag_groups if not isinstance(tag_groups, Exception) else {"error": str(tag_groups)},
    }


@mcp.tool()
async def my_tasks(status: str = "active") -> dict[str, Any]:
    """
    Get tasks assigned to the currently authenticated user (OAuth only).
    status: active | done | all
    """
    user_info = await _call("me")
    email = (user_info.get("data", {}).get("email") or user_info.get("email", ""))
    p: dict = {}
    if status: p["status"] = status
    if email:  p["email_user_to"] = email
    tasks = await _call("get_all_tasks", **p)
    return {"current_user": user_info, "tasks": tasks}


@mcp.tool()
async def weekly_report() -> dict[str, Any]:
    """
    All time/cost entries logged in the current week (Monday → today).
    """
    date_from, date_to = _week_range()
    data = await _call("get_costs", date_from=date_from, date_to=date_to)
    return {"week": f"{date_from} — {date_to}", **data}


@mcp.tool()
async def find_overdue_tasks(project_id: Optional[str] = None) -> dict[str, Any]:
    """
    Find all active tasks whose deadline has already passed.
    Optionally filter by project.
    """
    p: dict = {"status": "active"}
    if project_id: p["id_project"] = project_id
    resp = await _call("get_tasks", **p)

    today = date.today()
    tasks_list = resp.get("data", [])
    if isinstance(tasks_list, dict):
        tasks_list = list(tasks_list.values())

    overdue = []
    for task in tasks_list:
        raw = task.get("dateend") or task.get("date_end") or ""
        d = _parse_ws_date(raw) if raw else None
        if d and d < today:
            task["days_overdue"] = (today - d).days
            overdue.append(task)

    overdue.sort(key=lambda t: t.get("days_overdue", 0), reverse=True)
    return {
        "checked_at":    today.isoformat(),
        "overdue_count": len(overdue),
        "tasks":         overdue,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MCP Resources  (contextual, read-only — Claude can pull these without a tool call)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.resource("worksection://projects")
async def resource_projects() -> str:
    """All projects in the account — use to resolve project IDs and names."""
    try:
        return json.dumps(await _call("get_projects"), ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("worksection://users")
async def resource_users() -> str:
    """All users in the account — use to resolve emails and IDs."""
    try:
        return json.dumps(await _call("get_users"), ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("worksection://project/{project_id}/tasks")
async def resource_project_tasks(project_id: str) -> str:
    """Active tasks for a specific project."""
    try:
        data = await _call("get_tasks", id_project=project_id, status="active")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("worksection://project/{project_id}/members")
async def resource_project_members(project_id: str) -> str:
    """Members of a specific project."""
    try:
        data = await _call("get_project_users", id_project=project_id)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("worksection://task/{task_id}")
async def resource_task(task_id: str) -> str:
    """Full task details including subtasks, comments, and files."""
    try:
        data = await _call("get_task", id_task=task_id, extra="subtasks,comments,files")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# MCP Prompts  (reusable prompt templates)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.prompt()
def daily_standup() -> str:
    """Daily standup: what I worked on, what's next, any blockers."""
    today = date.today().strftime("%d.%m.%Y")
    return (
        f"Сегодня {today}. Подготовь ежедневный стендап-отчёт:\n"
        "1. Вызови `my_tasks` — посмотри мои активные задачи.\n"
        "2. Вызови `weekly_report` — посмотри списанное время за эту неделю.\n"
        "3. Вызови `get_my_timer` — проверь, запущен ли таймер прямо сейчас.\n\n"
        "Сформируй краткий отчёт: что сделано, над чем работаю сейчас, есть ли блокеры."
    )


@mcp.prompt()
def project_report(project_id: str) -> str:
    """Full status report for a project."""
    return (
        f"Подготовь полный отчёт по проекту {project_id}:\n"
        "1. `get_project_summary` — общий обзор: детали, команда, теги.\n"
        "2. `find_overdue_tasks` — просроченные задачи.\n"
        f"3. `get_costs(project_id='{project_id}')` — трудозатраты.\n\n"
        "Сформируй структурированный отчёт: прогресс, команда, риски, что просрочено."
    )


@mcp.prompt()
def overdue_review(project_id: Optional[str] = None) -> str:
    """Find and triage all overdue tasks."""
    scope = f"в проекте {project_id}" if project_id else "по всем проектам"
    arg = f'project_id="{project_id}"' if project_id else ""
    return (
        f"Найди все просроченные задачи {scope}.\n"
        f"Вызови `find_overdue_tasks({arg})` и для каждой задачи выведи:\n"
        "- название, исполнитель, дедлайн, дней просрочки\n"
        "- рекомендацию: закрыть / перенести дедлайн / эскалировать\n\n"
        "В конце — общий счёт и приоритеты на сегодня."
    )


@mcp.prompt()
def weekly_summary() -> str:
    """Weekly time and progress summary."""
    date_from, date_to = _week_range()
    return (
        f"Подготовь итоги недели ({date_from} — {date_to}):\n"
        "1. `weekly_report` — сколько часов списано и на что.\n"
        "2. `get_all_tasks(status='done')` — что завершено на этой неделе.\n"
        "3. `find_overdue_tasks()` — что просрочено прямо сейчас.\n\n"
        "Сформируй краткий отчёт с метриками, выводами и приоритетами на следующую неделю."
    )


@mcp.prompt()
def task_breakdown(task_id: str) -> str:
    """Deep-dive analysis of a single task."""
    return (
        f"Сделай полный анализ задачи {task_id}:\n"
        f"1. `get_task_full('{task_id}')` — вся задача с комментариями, подзадачами, файлами и трудозатратами.\n"
        "Выведи:\n"
        "- статус, исполнитель, дедлайн, приоритет\n"
        "- прогресс подзадач (сколько закрыто из общего числа)\n"
        "- суммарное списанное время vs. max_time (план)\n"
        "- последние комментарии и открытые вопросы\n"
        "- рекомендация: всё ли идёт по плану?"
    )


# ── Mount & run ───────────────────────────────────────────────────────────────
app.mount("/mcp", _mcp_app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
