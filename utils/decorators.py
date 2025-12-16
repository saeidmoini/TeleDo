from __future__ import annotations

import asyncio
from functools import wraps

from logger import logger


def exception_decorator(func):
    """
    Decorator for handling exceptions in both synchronous and asynchronous functions.
    - If the wrapped function raises an exception, it will be logged instead of crashing the bot.
    - Returns None if an exception occurs, so the bot can continue running smoothly.
    """

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:  # pragma: no cover - logged path
            logger.error(f"Error in {func.__name__}: {e}")
            return None

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:  # pragma: no cover - logged path
            logger.error(f"Error in {func.__name__}: {e}")
            return None

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
