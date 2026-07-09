import pytest
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model


@pytest.mark.django_db
class TestProfile:

    def test_retrieve_profile(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get('/api/auth/profile')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['data']['email'] == 'user@example.com'
        assert 'password' not in data['data']

    def test_retrieve_profile_unauthenticated(self):
        client = APIClient()
        response = client.get('/api/auth/profile')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_display_name(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.patch('/api/auth/profile', {
            'display_name': 'Updated Name',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.display_name == 'Updated Name'

    def test_update_visibility_toggles_independently(self):
        User = get_user_model()
        user = User.objects.create_user(email='user@example.com', password='StrongPass123')
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.patch('/api/auth/profile', {
            'leaderboard_visible': False,
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.leaderboard_visible is False
        assert user.others_learning_visible is True

        response = client.patch('/api/auth/profile', {
            'others_learning_visible': False,
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.leaderboard_visible is False
        assert user.others_learning_visible is False
