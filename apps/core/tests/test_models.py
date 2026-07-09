import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
class TestUserModel:

    def test_creates_user_with_email(self):
        User = get_user_model()
        user = User.objects.create_user(email='test@example.com', password='strongpass123')
        assert user.email == 'test@example.com'
        assert user.check_password('strongpass123') is True

    def test_display_name_defaults_to_empty(self):
        User = get_user_model()
        user = User.objects.create_user(email='test@example.com', password='strongpass123')
        assert user.display_name == ''

    def test_leaderboard_visible_defaults_to_true(self):
        User = get_user_model()
        user = User.objects.create_user(email='test@example.com', password='strongpass123')
        assert user.leaderboard_visible is True

    def test_others_learning_visible_defaults_to_true(self):
        User = get_user_model()
        user = User.objects.create_user(email='test@example.com', password='strongpass123')
        assert user.others_learning_visible is True

    def test_visibility_toggles_are_independent(self):
        User = get_user_model()
        user = User.objects.create_user(email='test@example.com', password='strongpass123')
        user.leaderboard_visible = False
        user.save(update_fields=['leaderboard_visible'])
        user.refresh_from_db()
        assert user.leaderboard_visible is False
        assert user.others_learning_visible is True
