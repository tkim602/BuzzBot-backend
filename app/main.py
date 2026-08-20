"""BuzzBot FastAPI application."""

from __future__ import annotations

import uuid
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.api.agent import router as agent_router  # noqa: E402
from app.api.chat import router as chat_router  # noqa: E402
from app.api.health import router as health_router  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.graph.persistence import postgres_checkpointer  # noqa: E402

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.checkpointer = None
    async with AsyncExitStack() as stack:
        if settings.langgraph_checkpoint_enabled:
            try:
                application.state.checkpointer = await stack.enter_async_context(
                    postgres_checkpointer(settings.database_url_sync)
                )
            except Exception as exc:
                logger.error("langgraph checkpoint unavailable", error=type(exc).__name__)
        yield


app = FastAPI(
    title="BuzzBot",
    description="RAG chatbot for Georgia Tech campus information",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Attach a unique request ID for structured logging."""
    request_id = str(uuid.uuid4())[:8]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(health_router)
app.include_router(chat_router)
app.include_router(agent_router)
