# Pass 1: User-facing operability — Implementation Plan

> **Goal:** Deep health check, graceful shutdown config, and auto-retry alerting — everything that blocks real users from using the app.

**Architecture:** Three independent changes. Health check is a new view + three check functions (DB, Redis, Celery) with 5s in-memory cache. Shutdown config is two line changes (Dockerfile + base settings). Alerting adds Sentry CeleryIntegration + manual capture_message in FAILED paths.

**Tech Stack:** Django 5.1+, Celery 5.4, redis-py 6.4, sentry-sdk, Docker/Railway

## Global Constraints

- All 288 pytest tests must pass after each task.
- `--settings=config.settings.test` for all test commands.
- No unnecessary abstraction. Direct, production-valued changes only.

---

### Task 1: Deep health check

**Files:**
- Create: `apps/core/health.py`
- Modify: `config/urls.py:7-17`
- Create: `apps/core/tests/test_health.py`

**Interfaces:**
- Produces: `check_database() -> bool`, `check_redis() -> bool`, `check_celery() -> bool`, `get_health_status() -> dict`
- View: `health_check(request) -> JsonResponse` replaces current one-liner

- [ ] **Step 1: Write `apps/core/health.py`**

```python
import logging
import time
from typing import Any

import redis
from celery import current_app
from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)

_last_check_time: float = 0
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
```

- [ ] **Step 2: Replace health check view in `config/urls.py:7-17`**

```python
# Replace lines 7-8:
#   def health_check(request):
#       return JsonResponse({"status": "ok"})
# With:
from apps.core.health import get_health_status


def health_check(request):
    status = get_health_status()
    all_healthy = all(status.values())
    return JsonResponse({"status": "ok" if all_healthy else "degraded", "checks": status}, status=200 if all_healthy else 503)
```

- [ ] **Step 3: Write `apps/core/tests/test_health.py`**

```python
from unittest import mock

import pytest
from django.test import RequestFactory

from apps.core.health import (
    check_celery,
    check_database,
    check_redis,
    get_health_status,
)
from config.urls import health_check


class TestCheckDatabase:
    @pytest.mark.django_db
    def test_returns_true_when_db_alive(self):
        assert check_database() is True


class TestCheckRedis:
    def test_returns_false_when_no_redis(self):
        assert check_redis() is False

    @mock.patch("apps.core.health.redis.Redis.from_url")
    def test_returns_true_when_redis_pings(self, mock_from_url):
        mock_client = mock.Mock()
        mock_client.ping.return_value = True
        mock_from_url.return_value = mock_client
        assert check_redis() is True


class TestCheckCelery:
    def test_returns_false_when_no_workers(self):
        assert check_celery() is False

    @mock.patch("apps.core.health.current_app")
    def test_returns_true_when_workers_respond(self, mock_app):
        mock_inspect = mock.Mock()
        mock_inspect.stats.return_value = {"worker@host": {"pool": {}}}
        mock_app.control.inspect.return_value = mock_inspect
        assert check_celery() is True


class TestGetHealthStatus:
    @mock.patch("apps.core.health.check_database", return_value=True)
    @mock.patch("apps.core.health.check_redis", return_value=True)
    @mock.patch("apps.core.health.check_celery", return_value=True)
    def test_all_healthy(self, *_):
        result = get_health_status()
        assert result == {"database": True, "redis": True, "celery": True}

    @mock.patch("apps.core.health.check_database", return_value=True)
    @mock.patch("apps.core.health.check_redis", return_value=False)
    @mock.patch("apps.core.health.check_celery", return_value=True)
    def test_partial_degraded(self, *_):
        import apps.core.health as health_module
        health_module._last_check_time = 0
        result = get_health_status()
        assert result == {"database": True, "redis": False, "celery": True}

    def test_cache_reuses_result(self):
        import apps.core.health as health_module
        health_module._last_check_time = 0
        first = get_health_status()
        second = get_health_status()
        assert first is second


class TestHealthCheckView:
    @mock.patch("apps.core.health.check_database", return_value=True)
    @mock.patch("apps.core.health.check_redis", return_value=True)
    @mock.patch("apps.core.health.check_celery", return_value=True)
    def test_200_when_all_healthy(self, *_):
        import apps.core.health as health_module
        health_module._last_check_time = 0
        request = RequestFactory().get("/api/health/")
        response = health_check(request)
        assert response.status_code == 200
        data = response.json() if hasattr(response, "json") else __import__("json").loads(response.content)
        assert data["status"] == "ok"
        assert data["checks"] == {"database": True, "redis": True, "celery": True}

    @mock.patch("apps.core.health.check_database", return_value=True)
    @mock.patch("apps.core.health.check_redis", return_value=False)
    @mock.patch("apps.core.health.check_celery", return_value=False)
    def test_503_when_degraded(self, *_):
        import apps.core.health as health_module
        health_module._last_check_time = 0
        request = RequestFactory().get("/api/health/")
        response = health_check(request)
        assert response.status_code == 503
        data = response.json() if hasattr(response, "json") else __import__("json").loads(response.content)
        assert data["status"] == "degraded"
```

- [ ] **Step 4: Run tests**

```bash
pytest apps/core/tests/test_health.py -v --settings=config.settings.test
```

Expected: all tests pass.

- [ ] **Step 5: Run full suite to verify no regressions**

```bash
pytest --settings=config.settings.test
```

Expected: 288 tests pass (or more with new ones).

---

### Task 2: Graceful shutdown config

**Files:**
- Modify: `Dockerfile:18`
- Modify: `config/settings/base.py:173`

**Interfaces:**
- Dockerfile CMD adds `--graceful-timeout 60` to gunicorn.
- Base settings adds `CELERY_WORKER_SHUTDOWN_TIMEOUT = 60`.

- [ ] **Step 1: Update Dockerfile line 18**

```dockerfile
# Change:
CMD python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 4 --timeout 120
# To:
CMD python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 4 --timeout 120 --graceful-timeout 60
```

- [ ] **Step 2: Add Celery worker shutdown timeout in base settings after line 173**

```python
# After CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_WORKER_SHUTDOWN_TIMEOUT = 60
```

- [ ] **Step 3: Verify settings are accessible via Django**

```bash
python manage.py shell --settings=config.settings.test -c "from django.conf import settings; print(settings.CELERY_WORKER_SHUTDOWN_TIMEOUT); print(settings.CELERY_BEAT_SCHEDULER)"
```

Expected output: `60` and `django_celery_beat.schedulers:DatabaseScheduler`.

- [ ] **Step 4: Run full test suite**

```bash
pytest --settings=config.settings.test
```

---

### Task 3: Auto-retry exhaustion alerting

**Files:**
- Modify: `config/settings/production.py:9-19`
- Modify: `config/settings/development.py:11-22`
- Modify: `apps/learning/tasks.py:1-8,30-34,78-81`

**Interfaces:**
- Sentry init adds `CeleryIntegration()`.
- `generate_content_for_topic` calls `sentry_sdk.capture_message()` when marking FAILED.
- `review_content_for_topic` calls `sentry_sdk.capture_message()` when marking FAILED after review attempts exhausted.

- [ ] **Step 1: Add CeleryIntegration to Sentry init in production.py**

```python
# Line 6: add import
from sentry_sdk.integrations.celery import CeleryIntegration

# Line 13: add to integrations list
integrations=[DjangoIntegration(), CeleryIntegration(), sentry_logging],
```

- [ ] **Step 2: Same in development.py**

```python
# Line 6: add import
from sentry_sdk.integrations.celery import CeleryIntegration

# Line 16: add to integrations list
integrations=[DjangoIntegration(), CeleryIntegration(), sentry_logging],
```

- [ ] **Step 3: Add sentry_sdk import to tasks.py (after line 1)**

```python
# Line 1: Add import sentry_sdk after celery import
import sentry_sdk
from celery import shared_task
```

- [ ] **Step 4: Add capture_message in generate_content_for_topic FAILED path**

```python
# Lines 30-34: add sentry_sdk.capture_message before return
    except Exception:
        sentry_sdk.capture_message(
            f"Topic {topic_id}: content generation permanently failed",
            level="error",
        )
        Topic.objects.filter(id=topic_id).update(
            content_status=Topic.ContentStatus.FAILED,
            review_status=Topic.ReviewStatus.FLAGGED,
        )
        return
```

- [ ] **Step 5: Add capture_message in review_content_for_topic FAILED path (2 retries exhausted)**

```python
# Lines 77-81: add sentry_sdk.capture_message
    else:
        sentry_sdk.capture_message(
            f"Topic {topic_id}: all review attempts exhausted",
            level="error",
        )
        Topic.objects.filter(id=topic_id).update(
            content_status=Topic.ContentStatus.FAILED,
            review_status=Topic.ReviewStatus.FLAGGED,
        )
```

- [ ] **Step 6: Run full test suite**

```bash
pytest --settings=config.settings.test
```

---

### Verification

After all 3 tasks:

```bash
pytest --settings=config.settings.test
```

Expected: all tests pass with no regressions.
