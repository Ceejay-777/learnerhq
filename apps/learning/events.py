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


def publish_roadmap_status(subject) -> None:
    """Tell every enrolled user the current roadmap status of ``subject``.

    Delivery is best-effort: the database is the source of truth and clients
    reconcile with GET /subjects/<id>, so a publish failure must not fail the
    task that produced the state change.
    """
    from .models import Topic, UserSubjectProgress

    subject.refresh_from_db(fields=["roadmap_status"])
    payload = {
        "subject_id": subject.id,
        "roadmap_status": subject.roadmap_status,
        "topic_count": Topic.objects.filter(subject=subject).count(),
    }
    user_ids = UserSubjectProgress.objects.filter(subject=subject).values_list(
        "user_id", flat=True
    )
    for user_id in user_ids:
        try:
            send_event(user_channel(user_id), ROADMAP_STATUS_EVENT, payload)
        except Exception:
            logger.exception(
                "Failed to publish %s for subject %s to user %s",
                ROADMAP_STATUS_EVENT,
                subject.id,
                user_id,
            )
