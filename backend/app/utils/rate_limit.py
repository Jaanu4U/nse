import time
import logging
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import redis
from app.config import settings

logger = logging.getLogger(__name__)

# Initialize redis connection pool
try:
    redis_client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
except Exception as e:
    logger.warning(f"Failed to connect to Redis for Rate Limiter: {e}. Rate limiting will be disabled.")
    redis_client = None

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int = 100, window_seconds: int = 60):
        """
        :param limit: Maximum number of requests allowed within window_seconds
        :param window_seconds: Time window in seconds
        """
        super().__init__(app)
        self.limit = limit
        self.window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next):
        # Exclude docs, openapi.json, and metrics from rate limits
        path = request.url.path
        if path.startswith("/docs") or path.startswith("/openapi.json") or path.startswith("/redoc") or path.startswith("/api/v1/metrics"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown-ip"
        
        # If redis is disabled or not connected, bypass rate limiter
        if redis_client is None:
            return await call_next(request)

        try:
            now = time.time()
            key = f"rate_limit:{client_ip}"
            clear_before = now - self.window_seconds

            # Pipeline execution for sliding window
            pipe = redis_client.pipeline()
            pipe.zremrangebyscore(key, "-inf", clear_before)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, self.window_seconds)
            _, current_requests, _, _ = pipe.execute()

            if current_requests > self.limit:
                logger.warning(f"Rate limit exceeded for client {client_ip}. Requests in window: {current_requests}/{self.limit}")
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Too many requests. Please try again later."}
                )

        except redis.RedisError as re:
            # Fallback gracefully if Redis has connection problems during operation
            logger.error(f"Redis operation error in RateLimitMiddleware: {re}. Bypassing rate limiting.")

        return await call_next(request)
