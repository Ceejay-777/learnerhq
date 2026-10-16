import pytest
from unittest import mock
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from apps.core.models import PasswordResetToken


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    from django.core.cache import cache
    cache.clear()


@pytest.mark.django_db
class TestPasswordResetRequest:

    def _post(self, email='a@example.com'):
        return APIClient().post('/api/auth/password-reset/request', {'email': email}, format='json')

    def test_second_request_within_a_minute_is_throttled(self):
        assert self._post().status_code == 200
        response = self._post('A@example.com')
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert 0 < response.data['retry_after'] <= 60
        assert response['Retry-After'] == str(response.data['retry_after'])

    def test_wait_doubles_and_caps(self):
        now = 1_000_000.0
        waits = []
        with mock.patch('config.utils.throttling.time.time') as clock:
            for _ in range(9):
                clock.return_value = now
                assert self._post().status_code == 200
                clock.return_value = now + 1
                waits.append(self._post().data['retry_after'] + 1)
                now += waits[-1]
        assert waits == [60, 120, 240, 480, 960, 1920, 3600, 3600, 3600]

    def test_other_email_is_not_throttled(self):
        self._post()
        assert self._post('b@example.com').status_code == 200

    def test_concurrent_request_holding_the_lock_is_throttled(self):
        from django.core.cache import cache
        cache.add('throttle:password_reset:a@example.com:lock', 1, timeout=5)
        response = self._post()
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert response.data['retry_after'] == 1

    def test_ip_limit_is_30_per_hour(self):
        for i in range(30):
            assert self._post(f'u{i}@example.com').status_code == 200
        assert self._post('u30@example.com').status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_request_creates_token(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        client = APIClient()
        response = client.post('/api/auth/password-reset/request', {
            'email': 'user@example.com',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert PasswordResetToken.objects.filter(user=user).exists()

    def test_request_returns_success_even_for_nonexistent_email(self):
        client = APIClient()
        response = client.post('/api/auth/password-reset/request', {
            'email': 'nobody@example.com',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK

    @mock.patch('config.utils.tasks.send_password_reset_email.delay')
    def test_returns_200_when_email_dispatch_fails(self, mock_delay):
        mock_delay.side_effect = RuntimeError("Broker unreachable")
        User = get_user_model()
        User.objects.create_user(email='user@example.com', password='StrongPass123')
        client = APIClient()
        response = client.post('/api/auth/password-reset/request', {
            'email': 'user@example.com',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestPasswordResetConfirm:

    def test_confirm_with_valid_token_resets_password(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        token = PasswordResetToken.objects.create(user=user)
        client = APIClient()
        response = client.post('/api/auth/password-reset/confirm', {
            'email': 'user@example.com',
            'token': token.token,
            'password': 'NewStrongPass456',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.check_password('NewStrongPass456') is True

    def test_successful_reset_clears_request_backoff(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        client = APIClient()
        client.post('/api/auth/password-reset/request', {'email': 'user@example.com'}, format='json')
        assert client.post('/api/auth/password-reset/request', {'email': 'user@example.com'}, format='json').status_code == 429
        token = PasswordResetToken.objects.filter(user=user).first()
        client.post('/api/auth/password-reset/confirm', {
            'email': 'user@example.com', 'token': token.token, 'password': 'NewStrongPass456',
        }, format='json')
        assert client.post('/api/auth/password-reset/request', {'email': 'user@example.com'}, format='json').status_code == 200

    def test_confirm_deletes_token_after_use(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        token = PasswordResetToken.objects.create(user=user)
        client = APIClient()
        client.post('/api/auth/password-reset/confirm', {
            'email': 'user@example.com',
            'token': token.token,
            'password': 'NewStrongPass456',
        }, format='json')
        assert not PasswordResetToken.objects.filter(pk=token.pk).exists()

    def test_confirm_with_expired_token_fails(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        token = PasswordResetToken.objects.create(
            user=user,
            expires_at=timezone.now() - timedelta(hours=2),
        )
        client = APIClient()
        response = client.post('/api/auth/password-reset/confirm', {
            'email': 'user@example.com',
            'token': token.token,
            'password': 'NewStrongPass456',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_confirm_with_wrong_token_fails(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        PasswordResetToken.objects.create(user=user)
        client = APIClient()
        response = client.post('/api/auth/password-reset/confirm', {
            'email': 'user@example.com',
            'token': 'wrong-token',
            'password': 'NewStrongPass456',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestResetLinkAndSessions:

    @mock.patch('config.utils.email_notifications.send_text_email')
    def test_link_targets_frontend_with_encoded_u_and_s(self, mock_send, settings):
        from urllib.parse import parse_qs, urlsplit
        from config.utils.tasks import send_password_reset_email

        settings.FRONTEND_URL = 'https://app.example.com'
        send_password_reset_email('a+b@example.com', 'tok')
        link = next(w for w in mock_send.call_args.kwargs['body'].split() if w.startswith('http'))
        parts = urlsplit(link)
        assert f'{parts.scheme}://{parts.netloc}{parts.path}' == 'https://app.example.com/reset-password'
        assert parse_qs(parts.query) == {'u': ['a+b@example.com'], 's': ['tok']}

    def test_reset_invalidates_other_pending_tokens(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        used = PasswordResetToken.objects.create(user=user)
        other = PasswordResetToken.objects.create(user=user)
        APIClient().post('/api/auth/password-reset/confirm', {
            'email': 'user@example.com', 'token': used.token, 'password': 'NewStrongPass456',
        }, format='json')
        assert not PasswordResetToken.objects.filter(pk=other.pk).exists()

    def test_reset_revokes_existing_sessions(self):
        client = APIClient()
        client.post('/api/auth/signup', {
            'email': 'user@example.com', 'password': 'StrongPass123', 'display_name': 'U',
        }, format='json')
        old_cookies = {k: v.value for k, v in client.cookies.items()}
        assert client.get('/api/auth/profile').status_code == 200

        user = get_user_model().objects.get(email='user@example.com')
        token = PasswordResetToken.objects.create(user=user)
        APIClient().post('/api/auth/password-reset/confirm', {
            'email': 'user@example.com', 'token': token.token, 'password': 'NewStrongPass456',
        }, format='json')

        stale = APIClient()
        stale.cookies.load(old_cookies)
        assert stale.get('/api/auth/profile').status_code == 401
        assert stale.post('/api/auth/refresh').status_code == 401
