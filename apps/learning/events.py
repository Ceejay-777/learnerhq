"""Server-sent events for learning state changes.

Events go to a per-user channel (``user-<id>``). The channel manager in
``apps/core/eventstream.py`` only lets a user subscribe to their own channel,
so a client can never listen to anyone else's events.
"""

import logging

from django_eventstream import send_event

from apps.core.eventstream import user_channel

logger = logging.getLogger(__name__)

ROADMAP_STATUS_EVENT = "roadmap.status"
TOPIC_STATUS_EVENT = "topic.status"


def _publish_to_enrolled(subject_id: int, event: str, payload: dict) -> None:
    """Send ``payload`` to every user enrolled in the subject.

    Delivery is best-effort: the database is the source of truth and clients
    reconcile with a GET, so a publish failure must not fail the task that
    produced the state change.
    """
    from .models import UserSubjectProgress

    user_ids = UserSubjectProgress.objects.filter(subject_id=subject_id).values_list(
        "user_id", flat=True
    )
    for user_id in user_ids:
        try:
            send_event(user_channel(user_id), event, payload)
        except Exception:
            logger.exception(
                "Failed to publish %s for subject %s to user %s",
                event,
                subject_id,
                user_id,
            )


def publish_roadmap_status(subject) -> None:
    """Tell every enrolled user the current roadmap status of ``subject``."""
    from .models import Topic

    subject.refresh_from_db(fields=["roadmap_status"])
    _publish_to_enrolled(
        subject.id,
        ROADMAP_STATUS_EVENT,
        {
            "subject_id": subject.id,
            "roadmap_status": subject.roadmap_status,
            "topic_count": Topic.objects.filter(subject=subject).count(),
        },
    )


def publish_topic_status(topic_id: int) -> None:
    """Tell every enrolled user the current content status of a topic."""
    from .models import Topic

    topic = Topic.objects.get(id=topic_id)
    _publish_to_enrolled(
        topic.subject_id,
        TOPIC_STATUS_EVENT,
        {
            "subject_id": topic.subject_id,
            "topic_id": topic.id,
            "content_status": topic.content_status,
            "review_status": topic.review_status,
        },
    )
