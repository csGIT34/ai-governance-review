import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles

from app import auth, db, seed
from app.routes import ai, common, controls, issues
from app.web import render

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if db.engine() is None:
        db.init_engine()
    with db.SessionLocal() as session:
        seed.ensure_builtin(session)
        session.commit()
    yield


app = FastAPI(title="Governance Review", lifespan=lifespan)
app.middleware("http")(auth.csrf_guard)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
for module in (common, ai, controls, issues):
    app.include_router(module.router)


@app.exception_handler(HTTPException)
async def html_errors(request: Request, exc: HTTPException):
    """Friendly HTML error pages for browser navigation; JSON for API/fetch callers."""
    if "text/html" not in request.headers.get("accept", ""):
        return await http_exception_handler(request, exc)
    return render(request, "error.html", {"status": exc.status_code, "detail": exc.detail},
                  status_code=exc.status_code)


@app.get("/health")
def health():
    return {"status": "ok"}
