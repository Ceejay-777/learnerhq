import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from apps.learning.models import (
    Subject, Topic, TopicProgress, UserSubjectProgress,
)
from apps.learning.services import (
    global_leaderboard, topic_leaderboard, others_learning,
)

User = get_user_model()


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p", display_name="Alice")


@pytest.fixture
def auth_client(client, user):
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(user)
    client.cookies["access_token"] = str(refresh.access_token)
    return client


@pytest.fixture
def subject():
    return Subject.objects.create(name="World War II")


@pytest.fixture
def topic(subject):
    return Topic.objects.create(subject=subject, title="D-Day", level=1, order=1)


@pytest.fixture
def topic2(subject):
    return Topic.objects.create(subject=subject, title="Battle of Britain", level=2, order=2)


@pytest.mark.django_db
class TestGlobalLeaderboard:

    def test_returns_empty_when_no_users(self):
        result = global_leaderboard()
        assert result == []

    def test_returns_empty_when_no_points(self):
        User.objects.create_user(email="b@b.com", password="p")
        result = global_leaderboard()
        assert len(result) == 1
        assert result[0]["total_points"] == 0

    def test_orders_by_total_points_descending(self, user):
        user_b = User.objects.create_user(email="b@b.com", password="p", display_name="Bob")
        user_c = User.objects.create_user(email="c@c.com", password="p", display_name="Carol")
        subject = Subject.objects.create(name="Math")
        UserSubjectProgress.objects.create(user=user, subject=subject, points=100)
        UserSubjectProgress.objects.create(user=user_b, subject=subject, points=200)
        UserSubjectProgress.objects.create(user=user_c, subject=subject, points=50)

        result = global_leaderboard()
        assert len(result) == 3
        assert result[0]["user_id"] == user_b.id
        assert result[0]["total_points"] == 200
        assert result[1]["user_id"] == user.id
        assert result[1]["total_points"] == 100
        assert result[2]["user_id"] == user_c.id
        assert result[2]["total_points"] == 50

    def test_ranks_include_hidden_users_points(self, user):
        user_b = User.objects.create_user(email="b@b.com", password="p", display_name="Bob")
        user_c = User.objects.create_user(email="c@c.com", password="p", display_name="Carol")
        subject = Subject.objects.create(name="Math")
        UserSubjectProgress.objects.create(user=user, subject=subject, points=200)
        UserSubjectProgress.objects.create(user=user_b, subject=subject, points=100)
        UserSubjectProgress.objects.create(user=user_c, subject=subject, points=50)

        user.leaderboard_visible = False
        user.save()

        result = global_leaderboard()
        assert len(result) == 2
        # user_b has 100, user_c has 50, but user_a has 200 (hidden)
        # So user_b should be rank 2, user_c rank 3
        assert result[0]["user_id"] == user_b.id
        assert result[0]["rank"] == 2
        assert result[0]["total_points"] == 100
        assert result[1]["user_id"] == user_c.id
        assert result[1]["rank"] == 3
        assert result[1]["total_points"] == 50

    def test_hidden_user_not_in_list(self, user):
        user.leaderboard_visible = False
        user.save()
        subject = Subject.objects.create(name="Math")
        UserSubjectProgress.objects.create(user=user, subject=subject, points=100)

        result = global_leaderboard()
        user_ids = [r["user_id"] for r in result]
        assert user.id not in user_ids

    def test_single_user(self, user):
        subject = Subject.objects.create(name="Math")
        UserSubjectProgress.objects.create(user=user, subject=subject, points=75)

        result = global_leaderboard()
        assert len(result) == 1
        assert result[0]["user_id"] == user.id
        assert result[0]["total_points"] == 75
        assert result[0]["rank"] == 1

    def test_displays_name_or_email(self, user):
        User.objects.create_user(email="noname@b.com", password="p")
        subject = Subject.objects.create(name="Math")
        UserSubjectProgress.objects.create(user=user, subject=subject, points=10)

        result = global_leaderboard()
        names = [r["display_name"] for r in result]
        assert "Alice" in names
        assert "noname@b.com" in names

    def test_respects_limit(self, user):
        for i in range(10):
            u = User.objects.create_user(email=f"u{i}@b.com", password="p")
            s = Subject.objects.create(name=f"S{i}")
            UserSubjectProgress.objects.create(user=u, subject=s, points=i * 10)

        result = global_leaderboard(limit=3)
        assert len(result) == 3


@pytest.mark.django_db
class TestTopicLeaderboard:

    def test_returns_empty_when_no_progress(self, topic):
        user = User.objects.create_user(email="b@b.com", password="p")
        result = topic_leaderboard(topic.id)
        assert result == []

    def test_orders_by_points_descending(self, user, topic):
        user_b = User.objects.create_user(email="b@b.com", password="p", display_name="Bob")
        user_c = User.objects.create_user(email="c@c.com", password="p", display_name="Carol")
        TopicProgress.objects.create(user=user, topic=topic, points=100)
        TopicProgress.objects.create(user=user_b, topic=topic, points=200)
        TopicProgress.objects.create(user=user_c, topic=topic, points=50)

        result = topic_leaderboard(topic.id)
        assert len(result) == 3
        assert result[0]["user_id"] == user_b.id
        assert result[0]["points"] == 200
        assert result[1]["user_id"] == user.id
        assert result[1]["points"] == 100
        assert result[2]["user_id"] == user_c.id
        assert result[2]["points"] == 50

    def test_excludes_zero_point_progress(self, user, topic):
        TopicProgress.objects.create(user=user, topic=topic, points=0)
        result = topic_leaderboard(topic.id)
        assert result == []

    def test_ranks_include_hidden_users_points(self, user, topic):
        user_b = User.objects.create_user(email="b@b.com", password="p", display_name="Bob")
        user_c = User.objects.create_user(email="c@c.com", password="p", display_name="Carol")
        TopicProgress.objects.create(user=user, topic=topic, points=200)
        TopicProgress.objects.create(user=user_b, topic=topic, points=100)
        TopicProgress.objects.create(user=user_c, topic=topic, points=50)

        user.leaderboard_visible = False
        user.save()

        result = topic_leaderboard(topic.id)
        assert len(result) == 2
        assert result[0]["user_id"] == user_b.id
        assert result[0]["rank"] == 2
        assert result[1]["user_id"] == user_c.id
        assert result[1]["rank"] == 3

    def test_hidden_user_not_in_list(self, user, topic):
        user.leaderboard_visible = False
        user.save()
        TopicProgress.objects.create(user=user, topic=topic, points=100)

        result = topic_leaderboard(topic.id)
        assert result == []

    def test_only_returns_users_with_this_topic_points(self, user, topic, topic2):
        TopicProgress.objects.create(user=user, topic=topic, points=100)
        TopicProgress.objects.create(user=user, topic=topic2, points=50)

        result = topic_leaderboard(topic.id)
        assert len(result) == 1
        assert result[0]["points"] == 100


@pytest.mark.django_db
class TestOthersLearning:

    def test_returns_empty_when_no_others(self, user, topic):
        result = others_learning(topic.id, user)
        assert result == []

    def test_excludes_viewing_user(self, user, topic, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        TopicProgress.objects.create(user=user, topic=topic)
        result = others_learning(topic.id, user)
        assert result == []

    def test_excludes_others_learning_invisible_users(self, user, topic, subject):
        other = User.objects.create_user(
            email="b@b.com", password="p", display_name="Bob",
            others_learning_visible=False,
        )
        UserSubjectProgress.objects.create(user=user, subject=subject)
        UserSubjectProgress.objects.create(user=other, subject=subject, status=UserSubjectProgress.Status.ACTIVE)
        TopicProgress.objects.create(user=other, topic=topic, status=TopicProgress.Status.PASSED)

        result = others_learning(topic.id, user)
        user_ids = [r["user_id"] for r in result]
        assert other.id not in user_ids

    def test_prioritizes_active_similar_level_over_completed(self, user, topic, subject):
        other_bob = User.objects.create_user(
            email="b@b.com", password="p", display_name="Bob",
        )
        other_carol = User.objects.create_user(
            email="c@c.com", password="p", display_name="Carol",
        )

        UserSubjectProgress.objects.create(user=user, subject=subject, level_unlocked=2)
        UserSubjectProgress.objects.create(user=other_bob, subject=subject, status=UserSubjectProgress.Status.ACTIVE, level_unlocked=2)
        UserSubjectProgress.objects.create(user=other_carol, subject=subject, status=UserSubjectProgress.Status.COMPLETED, level_unlocked=3)

        TopicProgress.objects.create(user=other_bob, topic=topic, status=TopicProgress.Status.QUIZ_READY)
        TopicProgress.objects.create(user=other_carol, topic=topic, status=TopicProgress.Status.PASSED)

        result = others_learning(topic.id, user)
        assert len(result) == 2
        assert result[0]["user_id"] == other_bob.id
        assert result[1]["user_id"] == other_carol.id

    def test_skips_users_without_subject_progress(self, user, topic, subject):
        other = User.objects.create_user(email="b@b.com", password="p")
        UserSubjectProgress.objects.create(user=user, subject=subject)
        TopicProgress.objects.create(user=other, topic=topic)

        result = others_learning(topic.id, user)
        assert result == []

    def test_skips_non_active_non_completed_users(self, user, topic, subject):
        other = User.objects.create_user(email="b@b.com", password="p")
        UserSubjectProgress.objects.create(user=user, subject=subject)
        UserSubjectProgress.objects.create(user=other, subject=subject, status=UserSubjectProgress.Status.ACTIVE, level_unlocked=3)
        TopicProgress.objects.create(user=other, topic=topic, status=TopicProgress.Status.NOT_STARTED)

        result = others_learning(topic.id, user)
        assert result == []

    def test_similar_level_within_one(self, user, topic, subject):
        other = User.objects.create_user(email="b@b.com", password="p")
        UserSubjectProgress.objects.create(user=user, subject=subject, level_unlocked=2)
        UserSubjectProgress.objects.create(
            user=other, subject=subject, status=UserSubjectProgress.Status.ACTIVE, level_unlocked=3,
        )
        TopicProgress.objects.create(user=other, topic=topic, status=TopicProgress.Status.READING)

        result = others_learning(topic.id, user)
        assert len(result) == 1
        assert result[0]["user_id"] == other.id
        assert result[0]["status"] == UserSubjectProgress.Status.ACTIVE


@pytest.mark.django_db
class TestLeaderboardAPI:

    def test_global_leaderboard_requires_auth(self, client):
        resp = client.get("/api/learning/leaderboard")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_topic_leaderboard_requires_auth(self, client, topic):
        resp = client.get(f"/api/learning/topics/{topic.id}/leaderboard")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_others_learning_requires_auth(self, client, topic):
        resp = client.get(f"/api/learning/topics/{topic.id}/others-learning")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_global_leaderboard_returns_data(self, auth_client, user):
        subject = Subject.objects.create(name="Math")
        UserSubjectProgress.objects.create(user=user, subject=subject, points=100)
        resp = auth_client.get("/api/learning/leaderboard")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 1
        assert data[0]["total_points"] == 100

    def test_topic_leaderboard_returns_data(self, auth_client, user, topic):
        TopicProgress.objects.create(user=user, topic=topic, points=100)
        resp = auth_client.get(f"/api/learning/topics/{topic.id}/leaderboard")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 1
        assert data[0]["points"] == 100

    def test_others_learning_returns_data(self, auth_client, user, topic, subject):
        other = User.objects.create_user(email="b@b.com", password="p", display_name="Bob")
        UserSubjectProgress.objects.create(user=user, subject=subject)
        UserSubjectProgress.objects.create(
            user=other, subject=subject, status=UserSubjectProgress.Status.ACTIVE,
        )
        TopicProgress.objects.create(user=other, topic=topic, status=TopicProgress.Status.PASSED)
        resp = auth_client.get(f"/api/learning/topics/{topic.id}/others-learning")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 1
        assert data[0]["display_name"] == "Bob"
