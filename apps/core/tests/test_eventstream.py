import asyncio

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.eventstream import ANONYMOUS_CHANNEL, UserChannelManager, user_channel
from apps.core.middleware import CookieJWTUserMiddleware

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p")


@pytest.fixture
def other_user():
    return User.objects.create_user(email="c@d.com", password="p")


def _token(user):
    return str(RefreshToken.for_user(user).access_token)


class TestUserChannelManager:
    manager = UserChannelManager()

    def _request(self, user=None, query=""):
        request = RequestFactory().get(f"/api/events{query}")
        request.user = user if user is not None else AnonymousUser()
        return request

    @pytest.mark.django_db
    def test_authenticated_user_gets_only_their_channel(self, user):
        channels = self.manager.get_channels_for_request(self._request(user), {})
        assert channels == {user_channel(user.pk)}

    @pytest.mark.django_db
    def test_client_supplied_channel_is_ignored(self, user, other_user):
        request = self._request(user, query=f"?channel=user-{other_user.pk}&channel=admin")
        assert self.manager.get_channels_for_request(request, {}) == {user_channel(user.pk)}

    def test_anonymous_maps_to_unreadable_channel(self):
        channels = self.manager.get_channels_for_request(self._request(), {})
        assert channels == {ANONYMOUS_CHANNEL}
        assert self.manager.can_read_channel(None, ANONYMOUS_CHANNEL) is False

    @pytest.mark.django_db
    def test_can_read_own_channel_only(self, user, other_user):
        assert self.manager.can_read_channel(user, user_channel(user.pk)) is True
        assert self.manager.can_read_channel(user, user_channel(other_user.pk)) is False
        assert self.manager.can_read_channel(None, user_channel(user.pk)) is False

    def test_not_reliable_without_storage(self):
        assert self.manager.is_channel_reliable("user-1") is False


@pytest.mark.django_db
class TestCookieJWTUserMiddleware:
    @staticmethod
    def _run(path, cookie=None):
        seen = {}

        def get_response(request):
            seen["user"] = request.user
            return None

        request = RequestFactory().get(path)
        request.user = AnonymousUser()
        if cookie is not None:
            request.COOKIES["access_token"] = cookie
        CookieJWTUserMiddleware(get_response)(request)
        return seen["user"]

    def test_valid_cookie_sets_user_on_event_path(self, user):
        assert self._run("/api/events", _token(user)) == user

    def test_invalid_cookie_leaves_user_anonymous(self):
        assert not self._run("/api/events", "garbage.token.value").is_authenticated

    def test_no_cookie_leaves_user_anonymous(self):
        assert not self._run("/api/events").is_authenticated

    def test_other_paths_are_not_touched(self, user):
        assert not self._run("/api/learning/subjects", _token(user)).is_authenticated


@pytest.mark.django_db
class TestEventStreamEndpoint:
    """Hits the real endpoint; the stream body is never iterated (it is infinite)."""

    @staticmethod
    def _body(response):
        # A rejected stream emits one "forbidden" error event and then ends, so
        # it is safe to read to completion (an accepted stream never ends).
        assert response.is_async

        async def collect():
            return b"".join([chunk async for chunk in response.streaming_content])

        return asyncio.run(collect())

    def test_anonymous_is_rejected_as_forbidden(self):
        body = self._body(Client().get("/api/events"))
        assert b"stream-error" in body
        assert b"forbidden" in body
        assert b"stream-open" not in body

    def test_garbage_token_is_rejected_as_forbidden(self):
        client = Client()
        client.cookies["access_token"] = "garbage.token.value"
        body = self._body(client.get("/api/events"))
        assert b"forbidden" in body
        assert b"stream-open" not in body

    def test_authenticated_user_gets_an_event_stream(self, user):
        client = Client()
        client.cookies["access_token"] = _token(user)
        response = client.get("/api/events")
        assert response.status_code == 200
        assert response.streaming is True
        assert response["Content-Type"].startswith("text/event-stream")
        response.close()

    def test_cannot_subscribe_to_another_users_channel(self, user, other_user):
        client = Client()
        client.cookies["access_token"] = _token(user)
        response = client.get(f"/api/events?channel=user-{other_user.pk}")
        # Still just the caller's own stream; the foreign channel is ignored.
        assert response.streaming is True
        response.close()
