"""FastAPI app (plan 11). Lambda entry point: `handler` (Mangum)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from daybook.api import admin, auth, memories, sessions, settings_api
from daybook.api.deps import ApiError, error_response, optional_user, read_cookie
from daybook.engine.app import get_app
from daybook.obs.log import log_event, setup_logging

setup_logging()
log = logging.getLogger(__name__)
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    app = get_app()
    task = None
    if app.settings.is_local and app.settings.daybook_env != "test":
        task = asyncio.create_task(_local_ticker())  # EventBridge stand-in (cleanup + reminders)
    yield
    if task:
        task.cancel()


async def _local_ticker():
    from daybook.worker import run_job

    while True:
        await asyncio.sleep(60)
        try:
            await run_job(get_app(), {"job": "reminder_tick"})
        except Exception:
            log.exception("local tick failed")


_PROD = os.environ.get("DAYBOOK_ENV") == "prod"
api = FastAPI(  # no public API map in production
    title="Daybook",
    lifespan=lifespan,
    docs_url=None if _PROD else "/api/docs",
    redoc_url=None,
    openapi_url=None if _PROD else "/api/openapi.json",
)

# The only routes that answer without a signed session (DB-31). Everything else under /api/ is
# refused by `guard` before any route code runs, so a new route is private by default.
PUBLIC = {
    ("GET", "/api/health"),
    ("GET", "/api/me"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/google"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/demo/start"),
}
if not _PROD:
    PUBLIC |= {("GET", "/api/docs"), ("GET", "/api/openapi.json")}


@api.middleware("http")
async def guard(request: Request, call_next):
    s = get_app().settings
    path = request.url.path
    t0 = datetime.now(UTC)
    if s.origin_verify and request.headers.get("x-origin-verify") != s.origin_verify:
        return error_response(403, "forbidden", "Direct access isn't allowed.")
    if (
        request.method in MUTATING
        and path.startswith("/api/")
        and not path.endswith("/end")
        and request.headers.get("x-requested-with") != "daybook"
    ):
        return error_response(403, "forbidden", "Missing X-Requested-With header.")
    if (
        path.startswith("/api/")
        and (request.method, path) not in PUBLIC
        and read_cookie(request, get_app()) is None
    ):
        return error_response(401, "unauthorized", "Please sign in or start the demo.")
    response = await call_next(request)
    log_event(
        event="request",
        method=request.method,
        route=request.scope.get("route").path if request.scope.get("route") else path,
        status=response.status_code,
        ms=int((datetime.now(UTC) - t0).total_seconds() * 1000),
    )
    return response


@api.exception_handler(ApiError)
async def api_error(_: Request, exc: ApiError):
    return error_response(exc.status, exc.code, exc.message, **exc.extra)


@api.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return error_response(422, "validation", f"{where}: {first.get('msg', 'invalid')}".strip(": "))


@api.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    code = {404: "not_found", 405: "not_found", 401: "unauthorized", 403: "forbidden"}.get(
        exc.status_code, "internal"
    )
    return error_response(exc.status_code, code, str(exc.detail))


@api.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("unhandled error")
    return error_response(500, "internal", "Something went wrong on our side.")


@api.get("/api/health")
def health(request: Request, warm: int = 0):
    app = get_app()
    user = optional_user(request, app) if warm else None
    if user is not None:  # warm only the caller's own vault; anonymous calls do no work
        with app.vault(user).read():
            pass
    return {
        "ok": True,
        "env": app.settings.daybook_env,
        "llm": app.settings.daybook_llm,
        "tts": app.settings.daybook_tts if app.tts else "off",
    }


for r in (auth.router, sessions.router, memories.router, settings_api.router, admin.router):
    api.include_router(r, prefix="/api")

app = api


def handler(event, context):  # Lambda (Mangum), created lazily to keep cold starts small
    from mangum import Mangum

    return Mangum(api, lifespan="off")(event, context)
