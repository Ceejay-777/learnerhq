from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.ai.exceptions import ProviderError
from apps.learning.exceptions import GenerationError
from apps.learning.models import Subject, Topic, UserSubjectProgress
from apps.learning.services import (
    add_subject_to_user,
    check_level_progress,
    claim_roadmap_generation,
    resolve_or_create_subject,
    retry_roadmap,
)
from apps.learning.tasks import cleanup_stuck_generations, generate_roadmap_for_subject

User = get_user_model()
DELAY = "apps.learning.tasks.generate_roadmap_for_subject.delay"
SEND = "apps.learning.events.send_event"


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p")


@pytest.fixture
def other_user():
    return User.objects.create_user(email="c@d.com", password="p")


@pytest.fixture
def subject():
    return Subject.objects.create(name="Sourdough Baking")


def _auth_client(user):
    client = APIClient()
    client.cookies["access_token"] = str(RefreshToken.for_user(user).access_token)
    return client


def _make_topics(subject, count=3):
    for i in range(count):
        Topic.objects.create(subject=subject, title=f"T{i}", level=1, order=i + 1)


def _status(subject):
    subject.refresh_from_db()
    return subject.roadmap_status


@pytest.mark.django_db
class TestClaim:
    def test_claims_not_generated(self, subject):
        assert claim_roadmap_generation(subject.id) is True
        assert _status(subject) == Subject.RoadmapStatus.GENERATING
        assert subject.roadmap_started_at is not None

    def test_second_claim_loses(self, subject):
        assert claim_roadmap_generation(subject.id) is True
        assert claim_roadmap_generation(subject.id) is False

    def test_failed_can_be_reclaimed(self, subject):
        Subject.objects.filter(id=subject.id).update(roadmap_status="FAILED")
        assert claim_roadmap_generation(subject.id) is True

    def test_ready_cannot_be_claimed(self, subject):
        Subject.objects.filter(id=subject.id).update(roadmap_status="READY")
        assert claim_roadmap_generation(subject.id) is False


@pytest.mark.django_db
class TestAddSubjectEnrollsImmediately:
    def test_enrolls_and_dispatches_without_generating_inline(self, user, subject):
        with patch(DELAY) as delay, patch("apps.learning.services.generate_roadmap") as gen:
            usp = add_subject_to_user(user, subject)
        assert UserSubjectProgress.objects.filter(user=user, subject=subject).exists()
        assert usp.subject == subject
        delay.assert_called_once_with(subject.id)
        gen.assert_not_called()
        assert _status(subject) == Subject.RoadmapStatus.GENERATING

    def test_subject_with_topics_does_not_dispatch(self, user, subject):
        _make_topics(subject)
        with patch(DELAY) as delay:
            add_subject_to_user(user, subject)
        delay.assert_not_called()

    def test_second_user_while_generating_does_not_dispatch_again(self, user, other_user, subject):
        with patch(DELAY) as delay:
            add_subject_to_user(user, subject)
            add_subject_to_user(other_user, subject)
        assert delay.call_count == 1
        assert UserSubjectProgress.objects.filter(subject=subject).count() == 2

    def test_at_limit_does_not_claim_or_dispatch(self, user, subject):
        for i in range(5):
            UserSubjectProgress.objects.create(user=user, subject=Subject.objects.create(name=f"S{i}"))
        with patch(DELAY) as delay, pytest.raises(ValueError, match="limit of 5"):
            add_subject_to_user(user, subject)
        delay.assert_not_called()
        assert _status(subject) == Subject.RoadmapStatus.NOT_GENERATED

    def test_broker_down_marks_failed_but_keeps_enrollment(self, user, subject):
        with patch(DELAY, side_effect=BrokerOperationalError("down")), patch(SEND) as send:
            add_subject_to_user(user, subject)
        assert UserSubjectProgress.objects.filter(user=user, subject=subject).exists()
        assert _status(subject) == Subject.RoadmapStatus.FAILED
        assert send.call_args.args[2]["roadmap_status"] == "FAILED"


@pytest.mark.django_db
class TestRoadmapTask:
    def _enroll(self, user, subject):
        with patch(DELAY):
            add_subject_to_user(user, subject)

    def test_success_sets_ready_publishes_and_starts_content(self, user, subject):
        self._enroll(user, subject)

        def fake_generate(subj):
            _make_topics(subj, 4)

        with patch("apps.learning.services.generate_roadmap", side_effect=fake_generate), \
                patch("apps.learning.services.generate_initial_batch") as batch, \
                patch(SEND) as send:
            generate_roadmap_for_subject.apply(args=[subject.id])

        assert _status(subject) == Subject.RoadmapStatus.READY
        send.assert_called_once()
        channel, event_type, payload = send.call_args.args
        assert channel == f"user-{user.pk}"
        assert event_type == "roadmap.status"
        assert payload == {"subject_id": subject.id, "roadmap_status": "READY", "topic_count": 4}
        batch.assert_called_once()

    def test_publishes_to_every_enrolled_user(self, user, other_user, subject):
        self._enroll(user, subject)
        self._enroll(other_user, subject)
        with patch("apps.learning.services.generate_roadmap", side_effect=lambda s: _make_topics(s)), \
                patch("apps.learning.services.generate_initial_batch"), patch(SEND) as send:
            generate_roadmap_for_subject.apply(args=[subject.id])
        channels = {c.args[0] for c in send.call_args_list}
        assert channels == {f"user-{user.pk}", f"user-{other_user.pk}"}

    def test_redelivered_task_does_not_regenerate(self, user, subject):
        self._enroll(user, subject)
        _make_topics(subject)
        with patch("apps.learning.services.generate_roadmap") as gen, \
                patch("apps.learning.services.generate_initial_batch"), patch(SEND):
            generate_roadmap_for_subject.apply(args=[subject.id])
        gen.assert_not_called()
        assert _status(subject) == Subject.RoadmapStatus.READY

    def test_generation_error_marks_failed_and_notifies(self, user, subject):
        self._enroll(user, subject)
        with patch("apps.learning.services.generate_roadmap", side_effect=GenerationError("bad", phase="roadmap_validation")), \
                patch(SEND) as send:
            generate_roadmap_for_subject.apply(args=[subject.id])
        assert _status(subject) == Subject.RoadmapStatus.FAILED
        assert send.call_args.args[2]["roadmap_status"] == "FAILED"

    def test_provider_error_propagates_and_stays_generating_until_last_retry(self, user, subject):
        self._enroll(user, subject)
        with patch("apps.learning.services.generate_roadmap", side_effect=ProviderError("x", provider="gemini")), \
                patch(SEND) as send:
            generate_roadmap_for_subject.push_request(retries=0)
            try:
                with pytest.raises(ProviderError):
                    generate_roadmap_for_subject.run(subject.id)
            finally:
                generate_roadmap_for_subject.pop_request()
        assert _status(subject) == Subject.RoadmapStatus.GENERATING
        send.assert_not_called()

    def test_provider_error_on_last_retry_marks_failed(self, user, subject):
        self._enroll(user, subject)
        with patch("apps.learning.services.generate_roadmap", side_effect=ProviderError("x", provider="gemini")), \
                patch(SEND) as send:
            generate_roadmap_for_subject.push_request(retries=generate_roadmap_for_subject.max_retries)
            try:
                with pytest.raises(ProviderError):
                    generate_roadmap_for_subject.run(subject.id)
            finally:
                generate_roadmap_for_subject.pop_request()
        assert _status(subject) == Subject.RoadmapStatus.FAILED
        assert send.call_args.args[2]["roadmap_status"] == "FAILED"

    def test_publish_failure_does_not_fail_the_task(self, user, subject):
        self._enroll(user, subject)
        with patch("apps.learning.services.generate_roadmap", side_effect=lambda s: _make_topics(s)), \
                patch("apps.learning.services.generate_initial_batch"), \
                patch(SEND, side_effect=ConnectionError("redis down")):
            result = generate_roadmap_for_subject.apply(args=[subject.id])
        assert result.successful()
        assert _status(subject) == Subject.RoadmapStatus.READY


@pytest.mark.django_db
class TestCleanupStuckRoadmaps:
    def test_old_generating_becomes_failed_and_notifies(self, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        Subject.objects.filter(id=subject.id).update(
            roadmap_status="GENERATING", roadmap_started_at=timezone.now() - timedelta(hours=2)
        )
        with patch(SEND) as send:
            cleanup_stuck_generations()
        assert _status(subject) == Subject.RoadmapStatus.FAILED
        assert send.call_args.args[2]["roadmap_status"] == "FAILED"

    def test_recent_generating_is_untouched(self, subject):
        Subject.objects.filter(id=subject.id).update(
            roadmap_status="GENERATING", roadmap_started_at=timezone.now() - timedelta(minutes=5)
        )
        cleanup_stuck_generations()
        assert _status(subject) == Subject.RoadmapStatus.GENERATING


@pytest.mark.django_db
class TestRetryRoadmap:
    def test_not_enrolled_raises(self, user, subject):
        with pytest.raises(UserSubjectProgress.DoesNotExist):
            retry_roadmap(user, subject.id)

    def test_failed_is_redispatched(self, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        Subject.objects.filter(id=subject.id).update(roadmap_status="FAILED")
        with patch(DELAY) as delay:
            result = retry_roadmap(user, subject.id)
        delay.assert_called_once_with(subject.id)
        assert result.roadmap_status == Subject.RoadmapStatus.GENERATING

    def test_already_generating_is_not_redispatched(self, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        Subject.objects.filter(id=subject.id).update(roadmap_status="GENERATING")
        with patch(DELAY) as delay:
            retry_roadmap(user, subject.id)
        delay.assert_not_called()

    def test_subject_with_topics_is_not_regenerated(self, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        _make_topics(subject)
        with patch(DELAY) as delay:
            retry_roadmap(user, subject.id)
        delay.assert_not_called()


@pytest.mark.django_db
class TestEmptyRoadmapGuards:
    def test_level_check_with_no_topics_is_a_noop(self, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        assert check_level_progress(user, subject).action == "none"


@pytest.mark.django_db
class TestCreateSubjectViewFlow:
    URL = "/api/learning/subjects/create"

    def test_limit_checked_before_any_ai_work(self, user):
        for i in range(5):
            UserSubjectProgress.objects.create(user=user, subject=Subject.objects.create(name=f"S{i}"))
        with patch("apps.learning.views.resolve_or_create_subject") as resolve:
            resp = _auth_client(user).post(self.URL, {"name": "Astronomy"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "limit of 5" in resp.data["detail"]
        resolve.assert_not_called()

    def test_already_enrolled_name_passes_the_limit_check(self, user):
        subjects = [Subject.objects.create(name=f"S{i}") for i in range(5)]
        for s in subjects:
            UserSubjectProgress.objects.create(user=user, subject=s)
            _make_topics(s, 1)
        resp = _auth_client(user).post(self.URL, {"name": "s0"}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["data"]["subject"]["name"] == "S0"

    def test_new_subject_returns_generating_without_waiting(self, user, subject):
        with patch("apps.learning.views.resolve_or_create_subject") as resolve, \
                patch(DELAY) as delay, patch("apps.learning.services.generate_roadmap") as gen:
            resolve.return_value.action = "resolve"
            resolve.return_value.subject = subject
            resp = _auth_client(user).post(self.URL, {"name": "Sourdough"}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["data"]["subject"]["roadmap_status"] == "GENERATING"
        delay.assert_called_once_with(subject.id)
        gen.assert_not_called()


@pytest.mark.django_db
class TestRoadmapStatusInResponses:
    def test_detail_and_list_expose_roadmap_status(self, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        Subject.objects.filter(id=subject.id).update(roadmap_status="GENERATING")
        client = _auth_client(user)
        detail = client.get(f"/api/learning/subjects/{subject.id}")
        listing = client.get("/api/learning/subjects")
        assert detail.data["data"]["roadmap_status"] == "GENERATING"
        assert detail.data["data"]["topics"] == []
        assert listing.data["data"][0]["roadmap_status"] == "GENERATING"

    def test_retry_endpoint(self, user, subject):
        UserSubjectProgress.objects.create(user=user, subject=subject)
        Subject.objects.filter(id=subject.id).update(roadmap_status="FAILED")
        client = _auth_client(user)
        with patch(DELAY) as delay:
            resp = client.post(f"/api/learning/subjects/{subject.id}/roadmap/retry")
        assert resp.status_code == 200
        assert resp.data["data"]["roadmap_status"] == "GENERATING"
        delay.assert_called_once()

    def test_retry_endpoint_404_when_not_enrolled(self, user, subject):
        resp = _auth_client(user).post(f"/api/learning/subjects/{subject.id}/roadmap/retry")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestResolveGetOrCreate:
    """Standardizing a new input to an existing subject name must not 500."""

    @patch("apps.learning.services.standardize_subject_name")
    @patch("apps.learning.services._find_similar_subjects", return_value=[])
    @patch("apps.learning.services.generate_embedding", return_value=[0.1] * 3072)
    def test_standardized_name_matching_existing_subject(self, _embed, _find, standardize):
        existing = Subject.objects.create(name="Python Programming")
        standardize.return_value = {"is_specific": True, "standardized_name": "Python Programming"}
        result = resolve_or_create_subject("python programing")
        assert result.action == "resolve"
        assert result.subject.id == existing.id
        assert Subject.objects.count() == 1

    @patch("apps.learning.services.standardize_subject_name")
    @patch("apps.learning.services._find_similar_subjects", return_value=[])
    @patch("apps.learning.services.generate_embedding", return_value=[0.1] * 3072)
    def test_standardized_name_differing_only_by_case(self, _embed, _find, standardize):
        existing = Subject.objects.create(name="Python Programming")
        standardize.return_value = {"is_specific": True, "standardized_name": "python programming"}
        result = resolve_or_create_subject("pythn")
        assert result.subject.id == existing.id
        assert Subject.objects.count() == 1
