"""Who is calling, and what they may do.

Identity: Entra ID via Azure Container Apps / App Service built-in auth ("Easy Auth"),
which puts the signed-in user in X-MS-CLIENT-PRINCIPAL-* headers. Roles are stored in
the users table and managed in-app by admins (no Entra app-role setup needed).
"""
import base64
import json
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, config
from app.db import get_session
from app.models import User, aware, utcnow

ROLES = ["viewer", "preparer", "reviewer", "admin"]
ROLE_HELP = {
    "viewer": "read-only",
    "preparer": "answer items, attach evidence, raise issues",
    "reviewer": "preparer + sign off responses, record decisions, close assessments",
    "admin": "reviewer + manage users, libraries, scope and settings",
}

# Local development users, selectable from the nav when AUTH_MODE=dev.
DEV_USERS = [
    ("alice@dev.local", "Alice (admin)", "admin"),
    ("bob@dev.local", "Bob (reviewer)", "reviewer"),
    ("carol@dev.local", "Carol (preparer)", "preparer"),
    ("victor@dev.local", "Victor (viewer)", "viewer"),
]


def _easyauth_identity(request: Request) -> tuple[str, str]:
    upn = (request.headers.get("x-ms-client-principal-name") or "").strip().lower()
    name = ""
    raw = request.headers.get("x-ms-client-principal")
    if raw:
        try:
            claims = json.loads(base64.b64decode(raw))["claims"]
            name = next((c["val"] for c in claims if c.get("typ") == "name"), "")
        except (ValueError, KeyError, TypeError):
            pass
    return upn, name


def _identity(request: Request) -> tuple[str, str]:
    if config.AUTH_MODE == "dev":
        upn = request.cookies.get("dev_user") or DEV_USERS[0][0]
        return upn, next((n for u, n, _ in DEV_USERS if u == upn), upn)
    return _easyauth_identity(request)


def current_user(request: Request, db: Session = Depends(get_session)) -> User:
    upn, name = _identity(request)
    if not upn:
        raise HTTPException(401, "Not signed in")
    user = db.scalar(select(User).where(User.upn == upn))
    if user is None:
        role = "admin" if upn in config.ADMIN_UPNS else config.DEFAULT_ROLE
        user = audit.create(db, upn, User(upn=upn, display_name=name or upn, role=role),
                            note="first sign-in")
    if not user.active:
        raise HTTPException(403, "Your account is deactivated")
    if user.last_seen_at is None or utcnow() - aware(user.last_seen_at) > timedelta(minutes=5):
        user.last_seen_at = utcnow()
    db.commit()
    request.state.user = user
    return user


def can(user: User, role: str) -> bool:
    return ROLES.index(user.role) >= ROLES.index(role)


def require(role: str):
    """Dependency: the caller must hold at least `role`."""
    def dep(user: User = Depends(current_user)) -> User:
        if not can(user, role):
            raise HTTPException(403, f"Requires the {role} role (you are {user.role})")
        return user
    return dep


async def csrf_guard(request: Request, call_next):
    """Reject cross-site state-changing requests (Fetch Metadata, with an Origin
    fallback for browsers that don't send it). Easy Auth uses a session cookie,
    so without this any site could post forms as the signed-in user."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if site and site not in ("same-origin", "none"):
            return PlainTextResponse("Cross-site request blocked", status_code=403)
        if not site and origin and urlparse(origin).netloc != host:
            return PlainTextResponse("Cross-origin request blocked", status_code=403)
    return await call_next(request)


def ensure_dev_users(db: Session):
    if config.AUTH_MODE != "dev":
        return
    for upn, name, role in DEV_USERS:
        if not db.scalar(select(User).where(User.upn == upn)):
            audit.create(db, "system", User(upn=upn, display_name=name, role=role),
                         note="dev seed")
