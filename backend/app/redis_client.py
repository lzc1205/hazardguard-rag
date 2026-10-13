import redis.asyncio as aioredis
from app.config import settings
from app.utils.logger import logger

redis_client: aioredis.Redis | None = None

async def get_redis() -> aioredis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connected")
    return redis_client

async def close_redis():
    global redis_client
    if redis_client is not None:
        await redis_client.close()
        redis_client = None
        logger.info("Redis connection closed")
