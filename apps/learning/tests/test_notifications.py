import pytest
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.learning.models import Subject, Topic, UserSubjectProgress
from apps.learning.services import (
    advance_due_time,
    get_due_notifications,
    set_notification_frequency,
)
from apps.learning.tasks import dispatch_notifications, send_notification

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
    return Subject.objects.create(name="World War II")


@pytest.fixture
def usp(user, subject):
    return UserSubjectProgress.objects.create(
        user=user,
        subject=subject,
        notification_frequency_hours=24,
        next_due_at=timezone.now() - timedelta(hours=1),
    )


@pytest.mark.django_db
class TestSetNotificationFrequency:

    def test_sets_frequency_and_updates_next_due(self, user, subject, usp):
        result = set_notification_frequency(user, subject, 12)
        assert result.notification_frequency_hours == 12
        assert result.next_due_at is not None
        assert result.next_due_at > timezone.now()

    def test_validates_hours_range(self, user, subject, usp):
        with pytest.raises(ValueError, match="between 1 and 24"):
            set_notification_frequency(user, subject, 0)
        with pytest.raises(ValueError, match="between 1 and 24"):
            set_notification_frequency(user, subject, 25)

    def test_edge_values(self, user, subject, usp):
        r1 = set_notification_frequency(user, subject, 1)
        assert r1.notification_frequency_hours == 1
        r2 = set_notification_frequency(user, subject, 24)
        assert r2.notification_frequency_hours == 24


@pytest.mark.django_db
class TestGetDueNotifications:

    def test_returns_due_pairs(self, subject, usp):
        due = get_due_notifications()
        assert len(due) == 1
        assert due[0].id == usp.id

    def test_excludes_not_due(self, user, subject):
        UserSubjectProgress.objects.create(
            user=user, subject=subject,
            notification_frequency_hours=24,
            next_due_at=timezone.now() + timedelta(hours=1),
        )
        due = get_due_notifications()
        assert len(due) == 0

    def test_excludes_null_next_due(self, user, subject):
        UserSubjectProgress.objects.create(
            user=user, subject=subject,
            next_due_at=None,
        )
        due = get_due_notifications()
        assert len(due) == 0

    def test_excludes_completed_subjects(self, user, subject):
        UserSubjectProgress.objects.create(
            user=user, subject=subject,
            status=UserSubjectProgress.Status.COMPLETED,
            next_due_at=timezone.now() - timedelta(hours=1),
        )
        due = get_due_notifications()
        assert len(due) == 0

    def test_orders_by_next_due(self, user, subject):
        s2 = Subject.objects.create(name="Math")
        early = timezone.now() - timedelta(hours=2)
        later = timezone.now() - timedelta(hours=1)
        UserSubjectProgress.objects.create(
            user=user, subject=subject,
            next_due_at=early,
        )
        UserSubjectProgress.objects.create(
            user=user, subject=s2,
            next_due_at=later,
        )
        due = get_due_notifications()
        assert len(due) == 2
        assert due[0].next_due_at == early
        assert due[1].next_due_at == later


@pytest.mark.django_db
class TestAdvanceDueTime:

    def test_advances_from_original_time(self, usp):
        original = usp.next_due_at
        advance_due_time(usp, original)
        usp.refresh_from_db()
        expected = original + timedelta(hours=24)
        assert usp.next_due_at == expected

    def test_advance_ignores_actual_send_time(self, usp):
        original = usp.next_due_at
        fake_send_time = original + timedelta(minutes=30)
        advance_due_time(usp, original)
        usp.refresh_from_db()
        expected = original + timedelta(hours=24)
        assert usp.next_due_at == expected
        assert usp.next_due_at != fake_send_time + timedelta(hours=24)

    def test_advance_uses_current_frequency(self, user, subject, usp):
        usp.notification_frequency_hours = 6
        usp.save(update_fields=["notification_frequency_hours"])
        original = usp.next_due_at
        advance_due_time(usp, original)
        usp.refresh_from_db()
        expected = original + timedelta(hours=6)
        assert usp.next_due_at == expected


@pytest.mark.django_db
class TestDispatchNotifications:

    @patch("apps.learning.tasks.send_notification.delay")
    def test_fans_out_to_due_pairs(self, mock_send, subject, usp):
        dispatch_notifications()
        assert mock_send.call_count == 1
        assert mock_send.call_args[0][0] == usp.id

    @patch("apps.learning.tasks.send_notification.delay")
    def test_skips_when_none_due(self, mock_send, user, subject):
        UserSubjectProgress.objects.create(
            user=user, subject=subject,
            next_due_at=timezone.now() + timedelta(hours=1),
        )
        dispatch_notifications()
        assert mock_send.call_count == 0


@pytest.mark.django_db
class TestSendNotification:

    def test_content_ready_advances_from_original(self, subject, usp):
        Topic.objects.create(
            subject=subject, title="T1", level=1, order=1,
            content_status=Topic.ContentStatus.READY,
        )
        original_due = usp.next_due_at
        send_notification(usp.id)
        usp.refresh_from_db()
        expected = original_due + timedelta(hours=24)
        assert usp.next_due_at == expected

    def test_no_topic_sets_next_due(self, user, subject):
        usp2 = UserSubjectProgress.objects.create(
            user=user, subject=subject,
            notification_frequency_hours=12,
            next_due_at=None,
        )
        send_notification(usp2.id)
        usp2.refresh_from_db()
        assert usp2.next_due_at is not None
        assert usp2.next_due_at > timezone.now()

    def test_retry_triggers_generation_on_first_attempt(self, subject, usp):
        topic = Topic.objects.create(
            subject=subject, title="T1", level=1, order=1,
            content_status=Topic.ContentStatus.NOT_GENERATED,
        )
        original_due = usp.next_due_at

        with patch.object(send_notification, "retry") as mock_retry:
            mock_retry.side_effect = Exception("retry")
            from celery.exceptions import Retry
            mock_retry.side_effect = Retry()
            with patch("apps.learning.tasks.generate_content_for_topic.delay") as mock_gen:
                with pytest.raises(Retry):
                    send_notification(usp.id)
                assert mock_gen.called

    def test_content_becomes_ready_on_retry(self, subject, usp):
        topic = Topic.objects.create(
            subject=subject, title="T1", level=1, order=1,
            content_status=Topic.ContentStatus.NOT_GENERATED,
        )
        original_due = usp.next_due_at

        with patch.object(send_notification, "retry") as mock_retry:
            mock_retry.side_effect = Exception("retry")
            from celery.exceptions import Retry
            mock_retry.side_effect = Retry()
            with patch("apps.learning.tasks.generate_content_for_topic.delay"):
                with pytest.raises(Retry):
                    send_notification(usp.id)

        topic.content_status = Topic.ContentStatus.READY
        topic.save()

        send_notification(usp.id)
        usp.refresh_from_db()
        expected = original_due + timedelta(hours=24)
        assert usp.next_due_at == expected

    def test_retries_exhausted_sends_anyway(self, subject, usp):
        topic = Topic.objects.create(
            subject=subject, title="T1", level=1, order=1,
            content_status=Topic.ContentStatus.NOT_GENERATED,
        )
        original_due = usp.next_due_at

        with patch.object(send_notification, "retry") as mock_retry:
            mock_retry.side_effect = Exception("retry")
            from celery.exceptions import Retry
            mock_retry.side_effect = Retry()
            with patch("apps.learning.tasks.generate_content_for_topic.delay"):
                with pytest.raises(Retry):
                    send_notification(usp.id)

        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.GENERATING

    def test_missing_usp_does_not_crash(self):
        send_notification(99999)

    def test_delayed_send_does_not_cascade_schedule(self, subject, usp):
        """Advancement uses original due time, not send time."""
        Topic.objects.create(
            subject=subject, title="T1", level=1, order=1,
            content_status=Topic.ContentStatus.READY,
        )
        original_due = usp.next_due_at
        advance_due_time(usp, original_due)
        first_advance = original_due + timedelta(hours=24)
        usp.refresh_from_db()
        assert usp.next_due_at == first_advance

        advance_due_time(usp, first_advance)
        usp.refresh_from_db()
        second_advance = first_advance + timedelta(hours=24)
        assert usp.next_due_at == second_advance


@pytest.mark.django_db
class TestNotificationAPI:

    def test_set_frequency_requires_auth(self, client, subject):
        resp = client.patch(f"/api/learning/subjects/{subject.id}/notification-frequency", {"frequency_hours": 12}, content_type="application/json")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_set_frequency_works(self, auth_client, user, subject, usp):
        resp = auth_client.patch(f"/api/learning/subjects/{subject.id}/notification-frequency", {"frequency_hours": 6}, content_type="application/json")
        assert resp.status_code == status.HTTP_200_OK
        usp.refresh_from_db()
        assert usp.notification_frequency_hours == 6

    def test_set_frequency_validates_range(self, auth_client, subject, usp):
        resp = auth_client.patch(f"/api/learning/subjects/{subject.id}/notification-frequency", {"frequency_hours": 99}, content_type="application/json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_set_frequency_requires_field(self, auth_client, subject, usp):
        resp = auth_client.patch(f"/api/learning/subjects/{subject.id}/notification-frequency", {}, content_type="application/json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_set_frequency_rejects_non_int(self, auth_client, subject, usp):
        resp = auth_client.patch(f"/api/learning/subjects/{subject.id}/notification-frequency", {"frequency_hours": "abc"}, content_type="application/json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_notification_status_requires_auth(self, client, subject):
        resp = client.get(f"/api/learning/subjects/{subject.id}/notification-status")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_notification_status_returns_data(self, auth_client, user, subject, usp):
        resp = auth_client.get(f"/api/learning/subjects/{subject.id}/notification-status")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["data"]
        assert data["frequency_hours"] == 24
        assert data["next_due_at"] is not None

    def test_notification_status_missing_usp(self, auth_client, user, subject):
        resp = auth_client.get(f"/api/learning/subjects/{subject.id}/notification-status")
        assert resp.status_code == status.HTTP_404_NOT_FOUND
