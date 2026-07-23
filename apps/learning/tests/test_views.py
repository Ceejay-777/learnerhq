from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.learning.models import Subject, Topic, TopicProgress, UserSubjectProgress, UserInterest, QuizAttempt

User = get_user_model()


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p")


@pytest.fixture
def auth_client(client, user):
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(user)
    client.cookies["access_token"] = str(refresh.access_token)
    return client


@pytest.fixture
def subject():
    s = Subject.objects.create(name="World War II")
    for i in range(5):
        Topic.objects.create(subject=s, title=f"T{i}", level=1, order=i + 1)
    return s


@pytest.mark.django_db
class TestCheckLevelProgressView:

    def test_requires_auth(self, client, subject):
        resp = client.post(f"/api/learning/subjects/{subject.id}/progress/check")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_level_up(self, auth_client, user, subject):
        from apps.learning.models import TopicProgress
        UserSubjectProgress.objects.create(user=user, subject=subject)
        for t in Topic.objects.filter(subject=subject)[:4]:
            TopicProgress.objects.create(user=user, topic=t, status=TopicProgress.Status.PASSED)
        resp = auth_client.post(f"/api/learning/subjects/{subject.id}/progress/check")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert data["action"] == "level_up"
        assert data["new_level_unlocked"] == 2

    def test_subject_completed(self, auth_client, user, subject):
        from apps.learning.models import TopicProgress
        for i in range(3):
            Topic.objects.create(subject=subject, title=f"L2-T{i}", level=2, order=10 + i)
        for i in range(3):
            Topic.objects.create(subject=subject, title=f"L3-T{i}", level=3, order=20 + i)
        UserSubjectProgress.objects.create(user=user, subject=subject, level_unlocked=3)
        for t in Topic.objects.filter(subject=subject, level=3)[:3]:
            TopicProgress.objects.create(user=user, topic=t, status=TopicProgress.Status.PASSED)
        resp = auth_client.post(f"/api/learning/subjects/{subject.id}/progress/check")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert data["action"] == "subject_completed"
        assert data["needs_subject_selection"] is True
        assert len(data["suggestions"]) >= 0


@pytest.mark.django_db
class TestExploreSubjectsView:

    def test_returns_subjects(self, auth_client, subject):
        resp = auth_client.get("/api/learning/explore")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert len(data) >= 1
        assert data[0]["name"] == subject.name

    def test_requires_auth(self, client):
        resp = client.get("/api/learning/explore")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_annotates_user_state(self, auth_client, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        UserInterest.objects.create(user=user, subject=subject)
        resp = auth_client.get("/api/learning/explore")
        data = resp.json()["data"]
        entry = next(s for s in data if s["id"] == subject.id)
        assert entry["is_enrolled"] is True
        assert entry["is_interested"] is True


@pytest.mark.django_db
class TestMarkInterestView:

    def test_marks_interest(self, auth_client, user, subject):
        resp = auth_client.post(f"/api/learning/explore/{subject.id}/interest")
        assert resp.status_code == status.HTTP_201_CREATED
        assert UserInterest.objects.filter(user=user, subject=subject).exists()

    def test_remove_interest(self, auth_client, user, subject):
        UserInterest.objects.create(user=user, subject=subject)
        resp = auth_client.delete(f"/api/learning/explore/{subject.id}/interest")
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not UserInterest.objects.filter(user=user, subject=subject).exists()


@pytest.mark.django_db
class TestAddSubjectView:

    def test_adds_subject(self, auth_client, user, subject):
        resp = auth_client.post(f"/api/learning/subjects/{subject.id}/add")
        assert resp.status_code == status.HTTP_201_CREATED
        assert UserSubjectProgress.objects.filter(user=user, subject=subject).exists()

    def test_at_cap_raises_error(self, auth_client, user):
        for i in range(5):
            s = Subject.objects.create(name=f"S{i}")
            for j in range(3):
                Topic.objects.create(subject=s, title=f"T{j}", level=1, order=j + 1)
            UserSubjectProgress.objects.create(user=user, subject=s)
        extra = Subject.objects.create(name="Extra")
        Topic.objects.create(subject=extra, title="T", level=1, order=1)
        resp = auth_client.post(f"/api/learning/subjects/{extra.id}/add")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestRemoveSubjectView:

    def test_removes_subject(self, auth_client, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        resp = auth_client.delete(f"/api/learning/subjects/{subject.id}/remove")
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not UserSubjectProgress.objects.filter(user=user, subject=subject).exists()

    def test_remove_nonexistent_returns_404(self, auth_client, user, subject):
        resp = auth_client.delete(f"/api/learning/subjects/{subject.id}/remove")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestSubjectSuggestionsView:

    def test_returns_suggestions(self, auth_client, user, subject):
        resp = auth_client.get("/api/learning/subjects/suggestions")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert isinstance(data, list)


@pytest.mark.django_db
class TestGenerateQuizView:

    def test_requires_auth(self, client, subject):
        topic = Topic.objects.filter(subject=subject).first()
        resp = client.post(f"/api/learning/topics/{topic.id}/quiz/generate")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    @patch("apps.learning.views.generate_quiz")
    def test_generates_normal_quiz(self, mock_generate, auth_client, user, subject):
        topic = Topic.objects.filter(subject=subject).first()
        mock_generate.return_value = QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type="NORMAL", attempt_number=1,
            questions=[], total_points=100,
        )
        resp = auth_client.post(
            f"/api/learning/topics/{topic.id}/quiz/generate",
            {"quiz_type": "NORMAL"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert data["quiz_type"] == "NORMAL"
        assert data["attempt_number"] == 1
        assert data["total_points"] == 100

    @patch("apps.learning.views.generate_quiz")
    def test_generate_handles_value_error(self, mock_generate, auth_client, subject):
        topic = Topic.objects.filter(subject=subject).first()
        mock_generate.side_effect = ValueError("Normal quiz already passed for this topic")
        resp = auth_client.post(
            f"/api/learning/topics/{topic.id}/quiz/generate",
            {"quiz_type": "NORMAL"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "already passed" in resp.json()["detail"]


@pytest.mark.django_db
class TestSubmitQuizView:

    def test_requires_auth(self, client, subject):
        topic = Topic.objects.filter(subject=subject).first()
        resp = client.post(f"/api/learning/topics/{topic.id}/quiz/submit")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    @patch("apps.learning.views.submit_quiz")
    def test_submits_quiz(self, mock_submit, auth_client, user, subject):
        topic = Topic.objects.filter(subject=subject).first()
        mock_submit.return_value = QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type="NORMAL", attempt_number=1,
            questions=[], total_points=100, score=80, passed=True,
        )
        TopicProgress.objects.create(user=user, topic=topic)
        resp = auth_client.post(
            f"/api/learning/topics/{topic.id}/quiz/submit",
            {"attempt_id": 1, "answers": [0, 1, 2, 3]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert data["passed"] is True
        assert data["score"] == 80
        assert data["total_points"] == 100

    @patch("apps.learning.views.submit_quiz")
    def test_submit_handles_already_submitted(self, mock_submit, auth_client, subject):
        topic = Topic.objects.filter(subject=subject).first()
        mock_submit.side_effect = ValueError("Quiz already submitted")
        resp = auth_client.post(
            f"/api/learning/topics/{topic.id}/quiz/submit",
            {"attempt_id": 1, "answers": [0, 1, 2, 3]},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "already submitted" in resp.json()["detail"]


@pytest.mark.django_db
class TestMarkResourceLinksViewedView:

    def test_requires_auth(self, client, subject):
        topic = Topic.objects.filter(subject=subject).first()
        resp = client.post(f"/api/learning/topics/{topic.id}/resource-links-viewed")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_records_viewed_at(self, auth_client, user, subject):
        topic = Topic.objects.filter(subject=subject).first()
        resp = auth_client.post(f"/api/learning/topics/{topic.id}/resource-links-viewed")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert data["status"] == "NOT_STARTED"
        assert data["resource_links_viewed_at"] is not None
        tp = TopicProgress.objects.get(user=user, topic=topic)


@pytest.mark.django_db
class TestEnrolledSubjectsView:

    def test_requires_auth(self, client):
        resp = client.get("/api/learning/subjects")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_empty_when_no_enrollments(self, auth_client):
        resp = auth_client.get("/api/learning/subjects")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["data"] == []

    def test_returns_enrolled_subject_with_counts(self, auth_client, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        resp = auth_client.get("/api/learning/subjects")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert len(data) == 1
        entry = data[0]
        assert entry["id"] == subject.id
        assert entry["name"] == subject.name
        assert entry["status"] == "ACTIVE"
        assert entry["points"] == 0
        assert entry["level_unlocked"] == 1
        assert entry["topics_total"] == 5
        assert entry["topics_passed"] == 0
        assert entry["percent_complete"] == 0

    def test_counts_passed_topics(self, auth_client, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        tp = TopicProgress.objects.create(user=user, topic=Topic.objects.first(), status=TopicProgress.Status.PASSED)
        resp = auth_client.get("/api/learning/subjects")
        data = resp.json()["data"]
        assert data[0]["topics_passed"] == 1
        assert data[0]["percent_complete"] == 20

    def test_only_returns_own_enrollments(self, auth_client, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        other = get_user_model().objects.create_user(email="other@b.com", password="p")
        other_subj = Subject.objects.create(name="Other Subject")
        Topic.objects.create(subject=other_subj, title="T", level=1, order=1)
        UserSubjectProgress.objects.create(user=other, subject=other_subj)
        resp = auth_client.get("/api/learning/subjects")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == subject.id


@pytest.mark.django_db
class TestSubjectDetailView:

    def test_requires_auth(self, client, subject):
        resp = client.get(f"/api/learning/subjects/{subject.id}")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_404_when_not_enrolled(self, auth_client, subject):
        resp = auth_client.get(f"/api/learning/subjects/{subject.id}")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_404_for_nonexistent_subject(self, auth_client):
        resp = auth_client.get("/api/learning/subjects/99999")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_full_detail(self, auth_client, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        tp = Topic.objects.filter(subject=subject, order=1).first()
        TopicProgress.objects.create(user=user, topic=tp, status=TopicProgress.Status.PASSED, completed_at="2026-01-01T00:00:00Z")
        resp = auth_client.get(f"/api/learning/subjects/{subject.id}")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["status"] == "success"
        data = body["data"]
        assert data["id"] == subject.id
        assert data["name"] == subject.name
        assert data["status"] == "ACTIVE"
        assert data["level_unlocked"] == 1
        assert data["notification_frequency_hours"] == 24
        assert len(data["levels"]) == 3
        level1 = data["levels"][0]
        assert level1["level"] == 1
        assert level1["total"] == 5
        assert level1["passed"] == 1
        assert level1["is_unlocked"] is True
        assert len(data["topics"]) == 5
        first = data["topics"][0]
        assert first["order"] == 1
        assert first["is_passed"] is True
        assert first["user_progress_status"] == "PASSED"
        assert first["completed_at"] is not None
        last = data["topics"][4]
        assert last["order"] == 5
        assert last["is_passed"] is False
        assert last["user_progress_status"] is None
        assert last["completed_at"] is None

    def test_levels_unlocked_correctly(self, auth_client, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject, level_unlocked=2)
        resp = auth_client.get(f"/api/learning/subjects/{subject.id}")
        data = resp.json()["data"]
        assert data["levels"][0]["is_unlocked"] is True
        assert data["levels"][1]["is_unlocked"] is True
        assert data["levels"][2]["is_unlocked"] is False

    def test_empty_subject_without_topics(self, auth_client, user):
        s = Subject.objects.create(name="No Topics")
        UserSubjectProgress.objects.create(user=user, subject=s)
        resp = auth_client.get(f"/api/learning/subjects/{s.id}")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert len(data["topics"]) == 0
        assert data["levels"][0]["total"] == 0
        assert data["levels"][0]["threshold"] == 0


@pytest.mark.django_db
class TestSubjectPreviewView:

    def test_requires_auth(self, client, subject):
        resp = client.get(f"/api/learning/explore/{subject.id}")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_404_for_nonexistent_subject(self, auth_client):
        resp = auth_client.get("/api/learning/explore/99999")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_preview_for_unenrolled_user(self, auth_client, user, subject):
        resp = auth_client.get(f"/api/learning/explore/{subject.id}")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["status"] == "success"
        data = body["data"]
        assert data["id"] == subject.id
        assert data["name"] == subject.name
        assert data["is_enrolled"] is False
        assert data["is_completed"] is False
        assert data["is_interested"] is False
        assert data["enrollment_count"] == 0
        assert len(data["levels"]) == 3
        assert data["levels"][0]["level"] == 1
        assert data["levels"][0]["topic_count"] == 5
        assert "passed" not in data["levels"][0]
        assert "is_unlocked" not in data["levels"][0]
        assert len(data["topics"]) == 5
        first = data["topics"][0]
        assert first["id"] is not None
        assert first["title"].startswith("T")
        assert first["level"] == 1
        assert first["order"] == 1
        assert "user_progress_status" not in first

    def test_shows_enrolled_true_for_enrolled_user(self, auth_client, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        resp = auth_client.get(f"/api/learning/explore/{subject.id}")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert data["is_enrolled"] is True
        assert data["is_completed"] is False

    def test_shows_interested_true_when_marked(self, auth_client, user, subject):
        UserInterest.objects.create(user=user, subject=subject)
        resp = auth_client.get(f"/api/learning/explore/{subject.id}")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert data["is_interested"] is True

    def test_empty_subject_without_topics(self, auth_client, user):
        s = Subject.objects.create(name="No Topics")
        resp = auth_client.get(f"/api/learning/explore/{s.id}")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert len(data["topics"]) == 0
        assert data["levels"][0]["topic_count"] == 0
        assert data["levels"][1]["topic_count"] == 0
