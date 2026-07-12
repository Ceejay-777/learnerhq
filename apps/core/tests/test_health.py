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
    def test_returns_bool_and_does_not_raise(self):
        result = check_database()
        assert isinstance(result, bool)


class TestCheckRedis:
    def test_returns_bool_and_does_not_raise(self):
        result = check_redis()
        assert isinstance(result, bool)

    @mock.patch("apps.core.health.redis.Redis.from_url")
    def test_returns_false_when_redis_fails(self, mock_from_url):
        mock_client = mock.Mock()
        mock_client.ping.side_effect = ConnectionError
        mock_from_url.return_value = mock_client
        assert check_redis() is False

    @mock.patch("apps.core.health.redis.Redis.from_url")
    def test_returns_true_when_redis_pings(self, mock_from_url):
        mock_client = mock.Mock()
        mock_client.ping.return_value = True
        mock_from_url.return_value = mock_client
        assert check_redis() is True


class TestCheckCelery:
    def test_returns_bool_and_does_not_raise(self):
        result = check_celery()
        assert isinstance(result, bool)

    @mock.patch("apps.core.health.current_app")
    def test_returns_false_when_no_workers(self, mock_app):
        mock_app.control.inspect.return_value = None
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
        import apps.core.health as health_module
        health_module._last_check_time = 0
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
        import json
        data = json.loads(response.content)
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
        import json
        data = json.loads(response.content)
        assert data["status"] == "degraded"
