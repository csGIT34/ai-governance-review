"""Template rendering and redirect helpers shared by all routes."""
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import auth, config
from app.models import aware

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _fmt_dt(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value[:16].replace("T", " ")
    if isinstance(value, datetime):
        return aware(value).strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


templates.env.filters["dt"] = _fmt_dt
templates.env.filters["d"] = lambda v: _fmt_dt(v)[:10]
templates.env.globals["can"] = auth.can
templates.env.globals["config"] = config
templates.env.globals["DEV_USERS"] = auth.DEV_USERS


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    ctx = dict(ctx or {})
    ctx.setdefault("user", getattr(request.state, "user", None))
    ctx.setdefault("error", request.query_params.get("error", ""))
    ctx.setdefault("msg", request.query_params.get("msg", ""))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str, error: str = "", msg: str = "", anchor: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("error", error), ("msg", msg)) if v}
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    if anchor:
        url += f"#{anchor}"
    return RedirectResponse(url, status_code=303)
