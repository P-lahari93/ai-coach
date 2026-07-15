# FILE: backend/app/main.py
from __future__ import annotations
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.exceptions import AppError, CrisisContentDetectedError
from app.ai.safety_engine import CRISIS_RESOURCES
from app.middleware.logging import LoggingMiddleware
from app.middleware.request_id import RequestIDMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    redirect_slashes=False,
)

# ── Middleware ─────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(LoggingMiddleware)


# ── Exception handlers ─────────────────────────────────────────────────────────
@app.exception_handler(CrisisContentDetectedError)
async def crisis_content_handler(
    request: Request, exc: CrisisContentDetectedError
) -> JSONResponse:
    """
    Deliberately returns 200, not a 4xx — nothing technically "failed."
    The request was intentionally not carried forward to AI generation;
    the response body signals that via blocked=True + support_resources,
    so the frontend can render a supportive UI component rather than a
    generic error toast.
    """
    return JSONResponse(
        status_code=200,
        content={
            "blocked": True,
            "block_reason": "crisis_support",
            "message": (
                "We noticed something in your message that sounded like "
                "you might be going through a difficult time. You don't "
                "have to go through this alone — the resources below are "
                "here if you'd like support."
            ),
            "support_resources": CRISIS_RESOURCES,
        },
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.exception_handler(JWTError)
async def jwt_error_handler(request: Request, exc: JWTError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Invalid or expired token."},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred."},
    )


# ── Routes ─────────────────────────────────────────────────────────────────────
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "app": settings.APP_NAME,
    }