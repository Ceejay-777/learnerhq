from celery import shared_task
from django.db import models
from django.utils import timezone
from datetime import timedelta


@shared_task(name='learning.generate_content_for_topic')
def generate_content_for_topic(topic_id: int) -> None:
    from .models import Topic
    from .services import ensure_topic_content_ready, generate_topic_content
    from apps.ai.exceptions import ProviderError

    if not ensure_topic_content_ready(topic_id):
        return

    try:
        generate_topic_content(topic_id)
    except ProviderError:
        raise
    except Exception:
        Topic.objects.filter(id=topic_id).update(
            content_status=Topic.ContentStatus.FAILED,
            review_status=Topic.ReviewStatus.FLAGGED,
        )
        return

    review_content_for_topic.delay(topic_id)


@shared_task(name='learning.review_content_for_topic')
def review_content_for_topic(topic_id: int) -> None:
    from .models import Topic
    from .services import review_topic_content

    topic = Topic.objects.get(id=topic_id)

    try:
        passed = review_topic_content(topic_id)
    except Exception:
        raise

    if passed:
        Topic.objects.filter(id=topic_id).update(
            content_status=Topic.ContentStatus.READY,
            review_status=Topic.ReviewStatus.PASSED,
        )
        return

    Topic.objects.filter(id=topic_id).update(
        review_attempts=models.F("review_attempts") + 1,
    )
    topic.refresh_from_db()

    if topic.review_attempts < 2:
        Topic.objects.filter(id=topic_id).update(
            content_status=Topic.ContentStatus.NOT_GENERATED,
            generation_started_at=None,
        )
        generate_content_for_topic.delay(topic_id)
    else:
        Topic.objects.filter(id=topic_id).update(
            content_status=Topic.ContentStatus.FAILED,
            review_status=Topic.ReviewStatus.FLAGGED,
        )


@shared_task(name='learning.dispatch_notifications')
def dispatch_notifications():
    from .services import get_due_notifications
    due = get_due_notifications()
    for usp in due:
        send_notification.delay(usp.id)


@shared_task(bind=True, name='learning.send_notification', max_retries=2, default_retry_delay=300)
def send_notification(self, user_subject_progress_id):
    from datetime import timedelta

    from django.utils import timezone

    from .models import Topic, UserSubjectProgress
    from .services import advance_due_time

    import logging
    logger = logging.getLogger(__name__)

    try:
        usp = UserSubjectProgress.objects.select_related("subject").get(
            id=user_subject_progress_id,
        )
    except UserSubjectProgress.DoesNotExist:
        logger.warning("UserSubjectProgress %s not found, skipping", user_subject_progress_id)
        return

    original_due = usp.next_due_at

    next_topic = Topic.objects.filter(
        subject=usp.subject,
        content_status__in=(
            Topic.ContentStatus.NOT_GENERATED,
            Topic.ContentStatus.FAILED,
        ),
    ).order_by("order").first()

    if next_topic is not None:
        if self.request.retries == 0:
            from .services import ensure_topic_content_ready
            if next_topic.content_status == Topic.ContentStatus.FAILED:
                Topic.objects.filter(id=next_topic.id).update(
                    content_status=Topic.ContentStatus.NOT_GENERATED,
                )
                next_topic.refresh_from_db()
            if ensure_topic_content_ready(next_topic.id):
                generate_content_for_topic.delay(next_topic.id)

        next_topic.refresh_from_db()
        content_not_ready = next_topic.content_status not in (
            Topic.ContentStatus.READY,
            Topic.ContentStatus.FAILED,
        )

        if content_not_ready and self.request.retries < self.max_retries:
            raise self.retry()

    if original_due is not None:
        advance_due_time(usp, original_due)
    else:
        usp.refresh_from_db()
        usp.next_due_at = timezone.now() + timedelta(hours=usp.notification_frequency_hours)
        usp.save(update_fields=["next_due_at"])

    logger.info(
        "Notification sent for user %s on subject %s",
        usp.user_id, usp.subject_id,
    )


@shared_task(name='learning.cleanup_stuck_generations')
def cleanup_stuck_generations() -> None:
    from .models import Topic
    cutoff = timezone.now() - timedelta(hours=1)
    Topic.objects.filter(
        content_status=Topic.ContentStatus.GENERATING,
        generation_started_at__lt=cutoff,
    ).update(
        content_status=Topic.ContentStatus.NOT_GENERATED,
        generation_started_at=None,
    )


@shared_task(name='learning.auto_select_subjects')
def auto_select_subjects() -> None:
    from .models import Subject, UserSubjectProgress
    from .services import _generate_subject_suggestions, add_subject_to_user

    cutoff = timezone.now() - timedelta(hours=24)
    pending = UserSubjectProgress.objects.filter(
        needs_subject_selection=True,
        selection_pending_since__lt=cutoff,
    )
    for usp in pending:
        suggestions = _generate_subject_suggestions(usp.user)
        if not suggestions:
            continue
        top = Subject.objects.get(id=suggestions[0]["id"])
        try:
            add_subject_to_user(usp.user, top)
        except ValueError:
            continue
        usp.needs_subject_selection = False
        usp.save(update_fields=["needs_subject_selection"])
