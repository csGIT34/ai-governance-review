import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import auth, db, seed
from app.routes import ai, common, controls, issues, work
from app.web import render

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if db.engine() is None:
        db.init_engine()
    with db.SessionLocal() as session:
        seed.ensure_builtin(session)
        try:
            session.commit()
        except IntegrityError:  # another replica seeded at the same moment
            session.rollback()
    yield


app = FastAPI(title="Governance Review", lifespan=lifespan)
app.middleware("http")(auth.csrf_guard)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
for module in (common, ai, controls, issues, work):
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
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    """Readiness: the database answers. Probes call this without signing in, so
    exclude /health* from Easy Auth (see docs/AZURE_DEPLOYMENT.md)."""
    try:
        with db.engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as err:
        logging.getLogger(__name__).warning("readiness check failed: %s", err)
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}
