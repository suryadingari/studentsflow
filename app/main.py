from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.core.config import settings
from app.core.logging import configure_logging
from app.orchestration import create_demo_orchestrator
from app.api.routes import workflows
from app.db.session import SessionFactory


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging(settings.log_level)
    application.state.workflow_orchestrator = create_demo_orchestrator(
        sessions=SessionFactory, database_url=settings.database_url)
    yield


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
cors_origins = settings.cors_origin_list
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
app.include_router(health.router)
app.include_router(workflows.router)
