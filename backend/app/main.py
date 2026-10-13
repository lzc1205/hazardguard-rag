from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app.api.v1 import router as api_router
from app.middleware import RequestLogMiddleware
from app.utils.exceptions import AppException
from app.utils.logger import logger
from app.redis_client import close_redis

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("HazardGuard-RAG starting...")
    yield
    await close_redis()
    logger.info("HazardGuard-RAG shutdown")

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION, lifespan=lifespan, docs_url="/docs", redoc_url="/redoc")
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RequestLogMiddleware)
app.include_router(api_router)

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, "data": None, "errors": exc.errors})

@app.get("/health", tags=["system"])
async def health_check():
    return {"code": 0, "message": "ok", "data": {"status": "healthy"}}
