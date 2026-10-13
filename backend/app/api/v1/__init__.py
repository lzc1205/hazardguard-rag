from fastapi import APIRouter

from app.api.v1 import auth, chat, documents, knowledge, projects, reviews, users

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(projects.router)
router.include_router(reviews.router)
router.include_router(documents.router)
router.include_router(knowledge.router)
router.include_router(chat.router)

__all__ = ["router"]