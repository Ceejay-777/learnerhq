import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestSignUp:

    def test_signup_creates_user_and_returns_profile(self):
        client = APIClient()
        response = client.post('/api/auth/signup', {
            'email': 'new@example.com',
            'password': 'StrongPass123',
            'display_name': 'New User',
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data['status'] == 'success'
        assert data['data']['email'] == 'new@example.com'
        assert data['data']['display_name'] == 'New User'
        assert 'password' not in data['data']

    def test_signup_sets_auth_cookies(self):
        client = APIClient()
        response = client.post('/api/auth/signup', {
            'email': 'new@example.com',
            'password': 'StrongPass123',
        }, format='json')
        assert 'access_token' in response.cookies
        assert 'refresh_token' in response.cookies

    def test_signup_rejects_duplicate_email(self):
        client = APIClient()
        client.post('/api/auth/signup', {
            'email': 'dup@example.com', 'password': 'StrongPass123',
        }, format='json')
        response = client.post('/api/auth/signup', {
            'email': 'dup@example.com', 'password': 'StrongPass123',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()['status'] == 'error'

    def test_signup_rejects_missing_fields(self):
        client = APIClient()
        response = client.post('/api/auth/signup', {
            'email': 'missing@example.com',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_signup_rejects_weak_password(self):
        client = APIClient()
        response = client.post('/api/auth/signup', {
            'email': 'weak@example.com', 'password': '123',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestSignIn:

    def test_signin_with_valid_credentials(self):
        client = APIClient()
        client.post('/api/auth/signup', {
            'email': 'user@example.com', 'password': 'StrongPass123',
        }, format='json')
        response = client.post('/api/auth/signin', {
            'email': 'user@example.com', 'password': 'StrongPass123',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['data']['email'] == 'user@example.com'
        assert 'access_token' in response.cookies
        assert 'refresh_token' in response.cookies

    def test_signin_with_wrong_password(self):
        client = APIClient()
        client.post('/api/auth/signup', {
            'email': 'user@example.com', 'password': 'StrongPass123',
        }, format='json')
        response = client.post('/api/auth/signin', {
            'email': 'user@example.com', 'password': 'wrongpass',
        }, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_signin_with_nonexistent_email(self):
        client = APIClient()
        response = client.post('/api/auth/signin', {
            'email': 'nope@example.com', 'password': 'StrongPass123',
        }, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestRefreshToken:

    def test_refresh_with_valid_token(self):
        client = APIClient()
        client.post('/api/auth/signup', {
            'email': 'user@example.com', 'password': 'StrongPass123',
        }, format='json')
        response = client.post('/api/auth/refresh', format='json')
        assert response.status_code == status.HTTP_200_OK
        assert 'access_token' in response.cookies
        assert 'refresh_token' in response.cookies

    def test_refresh_returns_tokens_in_body(self):
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(
            email='body@example.com', password='StrongPass123',
        )
        refresh = RT.for_user(user)
        client = APIClient()
        client.cookies['refresh_token'] = str(refresh)
        response = client.post('/api/auth/refresh', format='json')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert 'access_token' in data
        assert 'refresh_token' in data
        assert isinstance(data['access_token'], str)
        assert isinstance(data['refresh_token'], str)
        assert len(data['access_token']) > 0
        assert len(data['refresh_token']) > 0

    def test_refresh_without_token_returns_error(self):
        client = APIClient()
        response = client.post('/api/auth/refresh', format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_error_has_no_tokens_in_body(self):
        client = APIClient()
        response = client.post('/api/auth/refresh', format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert 'access_token' not in data
        assert 'refresh_token' not in data


@pytest.mark.django_db
class TestSignOut:

    def test_signout_clears_cookies(self):
        client = APIClient()
        client.post('/api/auth/signup', {
            'email': 'user@example.com', 'password': 'StrongPass123',
        }, format='json')
        response = client.post('/api/auth/signout', format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.cookies['access_token'].value == ''
        assert response.cookies['refresh_token'].value == ''

    def test_signout_requires_authentication(self):
        client = APIClient()
        response = client.post('/api/auth/signout', format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
