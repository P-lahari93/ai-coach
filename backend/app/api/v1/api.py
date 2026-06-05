from __future__ import annotations
from fastapi import APIRouter
from app.api.v1.routers import auth, users, modules, sessions, feedback, knowledge, progress, analytics

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(modules.router, prefix="/modules", tags=["modules"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
api_router.include_router(progress.router, prefix="/progress", tags=["progress"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
