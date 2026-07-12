import logging
import time
from typing import Any

import redis
from celery import current_app
from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)

_last_check_time: float = 0.0
_last_check_result: dict[str, Any] = {}
_cache_seconds: float = 5.0


def _get_now() -> float:
    return time.monotonic()


def check_database() -> bool:
    try:
        connections["default"].cursor().execute("SELECT 1")
        return True
    except Exception:
        logger.exception("Database health check failed")
        return False


def check_redis() -> bool:
    try:
        r = redis.Redis.from_url(settings.CELERY_BROKER_URL)
        return r.ping()
    except Exception:
        logger.exception("Redis health check failed")
        return False


def check_celery() -> bool:
    try:
        stats = current_app.control.inspect(timeout=1.0)
        if stats is None:
            return False
        return bool(stats)
    except Exception:
        logger.exception("Celery health check failed")
        return False


def get_health_status() -> dict[str, Any]:
    global _last_check_time, _last_check_result
    now = _get_now()
    if (now - _last_check_time) < _cache_seconds:
        return _last_check_result
    result = {
        "database": check_database(),
        "redis": check_redis(),
        "celery": check_celery(),
    }
    _last_check_time = now
    _last_check_result = result
    return result
