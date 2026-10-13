import sys
from loguru import logger
from app.config import settings

logger.remove()
logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>", level="DEBUG" if settings.DEBUG else "INFO")
logger.add("logs/hazardguard_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", compression="zip", format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}", level="INFO")
__all__ = ["logger"]
