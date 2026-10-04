from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.api.routes import auth
from app.core.config import settings
from app.core.logging import configure_logging
from app.orchestration import create_demo_orchestrator
from app.api.routes import workflows
from app.db.session import SessionFactory


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging(settings.log_level)
    application.state.workflow_orchestrator = create_demo_orchestrator(
        sessions=SessionFactory, database_url=settings.database_url,
        real_crawl_for_live_workflows=True, allow_real_email=True)
    yield


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
cors_origins = settings.cors_origin_list
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(workflows.router)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if settings.app_env.lower() != "development":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
