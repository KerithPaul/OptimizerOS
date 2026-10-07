import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.v1.agents import router as agents_router
from app.api.v1.admin_users import router as admin_users_router
from app.api.v1.auth import router as auth_router
from app.api.v1.changes import router as changes_router
from app.api.v1.experiments import router as experiments_router
from app.api.v1.findings import router as findings_router
from app.api.v1.github import oauth_router as github_oauth_router
from app.api.v1.github import router as github_router
from app.api.v1.keyword_suggestions import router as keyword_suggestions_router
from app.api.v1.search_console import oauth_router as search_console_oauth_router
from app.api.v1.search_console import router as search_console_router
from app.api.v1.site_reports import router as site_reports_router
from app.api.v1.health import router as health_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.projects import router as projects_router
from app.api.v1.repositories import router as repositories_router
from app.api.v1.rollback import router as rollback_router
from app.api.v1.websites import router as websites_router
from app.api.v1.wordpress import router as wordpress_router
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.core.security import seed_user
from app.db.session import SessionLocal
from app.jobs import handlers  # noqa: F401  (import side effect: registers job handlers)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("architectos")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        seed_user(db, settings)
    finally:
        db.close()
    yield


class RequestIdMiddleware:
    """Pure ASGI middleware — deliberately not BaseHTTPMiddleware.

    BaseHTTPMiddleware deadlocks the whole worker when combined with a
    long-lived StreamingResponse (the job-events SSE endpoint): a client
    disconnect/reconnect on the stream can wedge its internal anyio stream
    and stop the worker from servicing any request, not just that one
    (encode/starlette#1012). A raw ASGI middleware has no such stream.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())
        token = request_id_var.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)


app = FastAPI(title="ArchitectOS API", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")
app.include_router(repositories_router, prefix="/api/v1")
app.include_router(websites_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(findings_router, prefix="/api/v1")
app.include_router(agents_router, prefix="/api/v1")
app.include_router(changes_router, prefix="/api/v1")
app.include_router(rollback_router, prefix="/api/v1")
app.include_router(github_router, prefix="/api/v1")
app.include_router(github_oauth_router, prefix="/api/v1")
app.include_router(wordpress_router, prefix="/api/v1")
app.include_router(search_console_router, prefix="/api/v1")
app.include_router(search_console_oauth_router, prefix="/api/v1")
app.include_router(keyword_suggestions_router, prefix="/api/v1")
app.include_router(experiments_router, prefix="/api/v1")
app.include_router(site_reports_router, prefix="/api/v1")
app.include_router(admin_users_router, prefix="/api/v1")
