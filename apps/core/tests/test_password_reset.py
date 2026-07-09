import pytest
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from apps.core.models import PasswordResetToken


@pytest.mark.django_db
class TestPasswordResetRequest:

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
