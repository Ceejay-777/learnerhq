import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, models, transaction
from django.db.models import Count, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError
from rest_framework.exceptions import PermissionDenied

from apps.ai.exceptions import ProviderError
from apps.ai.services import (
    generate_content,
    generate_embedding,
    rank_or_resolve,
    standardize_subject_name,
)

from .exceptions import GenerationError
from .models import (
    QuizAttempt,
    Subject,
    Topic,
    TopicProgress,
    UserInterest,
    UserSubjectProgress,
)

SUBJECT_LIMIT_MESSAGE = (
    "You've reached the limit of 5 active subjects. "
    "Remove one or complete a subject to add a new one."
)


@dataclass
class ResolveResult:
    action: Literal["resolve"]
    subject: Subject


@dataclass
class CreateResult:
    action: Literal["create"]
    standardized_name: str


@dataclass
class NarrowResult:
    action: Literal["narrow"]
    suggestion: str


@dataclass
class SubjectDetailResult:
    subject_id: int
    name: str
    status: str
    points: int
    level_unlocked: int
    created_at: datetime
    notification_frequency_hours: int
    next_due_at: datetime | None
    roadmap_status: str
    percent_complete: int
    levels: list[dict]
    topics: list[dict]


@dataclass
class SubjectPreviewResult:
    subject_id: int
    name: str
    enrollment_count: int
    is_enrolled: bool
    is_completed: bool
    is_interested: bool
    levels: list[dict]
    topics: list[dict]


def _compute_and_store_embedding(subject: Subject, text: str) -> None:
    if connection.vendor != "postgresql":
        return
        
    embedding = generate_embedding(text)
    Subject.objects.filter(id=subject.id).update(embedding=embedding)


def _find_similar_subjects(query_vector: list[float]) -> list[dict]:
    if connection.vendor != "postgresql":
        return []

    from pgvector.django import CosineDistance

    similar = (
        Subject.objects.filter(
            embedding__isnull=False,
        )
        .annotate(distance=CosineDistance("embedding", query_vector))
        .filter(distance__lte=0.15)
        .order_by("distance")[:5]
    )

    return [
        {"id": str(s.id), "name": s.name, "similarity_score": float(1 - s.distance)}
        for s in similar
    ]


def _get_or_create_subject(name: str) -> Subject:
    """Return the subject called ``name`` (case-insensitive), creating it if needed.

    The LLM may standardize a new input to the name of an existing subject, and
    two requests can race to create the same one, so a plain ``create`` would
    hit the unique constraint.
    """
    existing = Subject.objects.filter(name__iexact=name).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            subject = Subject.objects.create(name=name)
            _compute_and_store_embedding(subject, name)
        return subject
    except IntegrityError:
        return Subject.objects.get(name__iexact=name)


def check_enrollment_capacity(user, raw_name: str) -> None:
    """Fail fast, before any AI work, if the user has no free subject slot.

    A name the user is already enrolled in is allowed through: resolving it is
    an idempotent no-op that needs no new slot.
    """
    if get_available_slots(user) > 0:
        return
    if UserSubjectProgress.objects.filter(
        user=user, subject__name__iexact=raw_name
    ).exists():
        return
    raise ValueError(SUBJECT_LIMIT_MESSAGE)


def resolve_or_create_subject(raw_input: str) -> ResolveResult | NarrowResult:
    existing = Subject.objects.filter(name__iexact=raw_input).first()
    if existing is not None:
        return ResolveResult(action="resolve", subject=existing)

    query_vector = generate_embedding(raw_input)
    candidates = _find_similar_subjects(query_vector)

    if not candidates:
        llm_result = standardize_subject_name(raw_input)
        
        if not llm_result.get("is_specific", True):
            return NarrowResult(
                action="narrow",
                suggestion=llm_result.get("suggestion", "Please be more specific."),
            )
            
        standardized_name = llm_result["standardized_name"]
        subject = _get_or_create_subject(standardized_name)
        return ResolveResult(action="resolve", subject=subject)

    result = rank_or_resolve(raw_input, candidates)
    action = result.get("action")

    if action == "resolve":
        subject = Subject.objects.get(id=result["subject_id"])
        return ResolveResult(action="resolve", subject=subject)

    if action == "create":
        standardized_name = result["standardized_name"]
        subject = _get_or_create_subject(standardized_name)
        return ResolveResult(action="resolve", subject=subject)

    if action == "narrow":
        return NarrowResult(action="narrow", suggestion=result["suggestion"])

    raise GenerationError(
        f"Unexpected resolution action: {action}",
        phase="resolution",
    )


ROADMAP_SYSTEM_INSTRUCTION = (
    "You are an expert curriculum designer. You create structured, pedagogically sound "
    "learning roadmaps that progress from foundational concepts through intermediate skills "
    "to advanced mastery. Each topic should be scoped to a single sitting and ordered "
    "so every topic builds on what came before it. Avoid generic placeholder names — "
    "topics should be concrete and specific to the subject."
)

TOPIC_CONTENT_SYSTEM_INSTRUCTION = (
    "You are an educational content writer creating concise, engaging summaries for a "
    "self-paced learning platform. Write for a curious adult learner with no prior knowledge "
    "of the topic. Prioritize clarity and practical understanding over academic formalism. "
    "Every resource link should point to a real, publicly accessible resource that deepens "
    "understanding of the specific topic — not generic reference sites."
)

TOPIC_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "resource_links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["article", "video", "wikipedia"],
                    },
                },
                "required": ["title", "url", "type"],
            },
            "minItems": 2,
            "maxItems": 5,
        },
    },
    "required": ["summary", "resource_links"],
}

_VALID_TYPES = {"article", "video", "wikipedia"}


def _validate_topic_content(data: dict) -> dict:
    if not isinstance(data, dict):
        raise GenerationError(
            "Response is not a dict", phase="topic_content_validation"
        )
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise GenerationError(
            "Missing or empty 'summary'", phase="topic_content_validation"
        )
    links = data.get("resource_links")
    if not isinstance(links, list) or len(links) < 2 or len(links) > 5:
        raise GenerationError(
            f"Expected 2-5 resource_links, got {len(links) if isinstance(links, list) else 'invalid'}",
            phase="topic_content_validation",
        )
    for i, link in enumerate(links):
        if not isinstance(link, dict):
            raise GenerationError(
                f"Resource link {i} is not a dict", phase="topic_content_validation"
            )
        if not isinstance(link.get("title"), str) or not link["title"].strip():
            raise GenerationError(
                f"Resource link {i} missing valid title",
                phase="topic_content_validation",
            )
        if not isinstance(link.get("url"), str) or not link["url"].strip():
            raise GenerationError(
                f"Resource link {i} missing valid url", phase="topic_content_validation"
            )
        if link.get("type") not in _VALID_TYPES:
            raise GenerationError(
                f"Resource link {i} invalid type '{link.get('type')}'",
                phase="topic_content_validation",
            )
    return {"summary": summary.strip(), "resource_links": links}


def ensure_topic_content_ready(topic_id: int) -> bool:
    updated = Topic.objects.filter(
        id=topic_id,
        content_status=Topic.ContentStatus.NOT_GENERATED,
    ).update(
        content_status=Topic.ContentStatus.GENERATING,
        generation_started_at=timezone.now(),
    )
    return updated > 0


def request_topic_generation(topic_id: int) -> bool:
    """Queue content generation for a topic unless someone already has it.

    The claim is one atomic UPDATE, so any number of callers (page opens,
    scheduler, retries) queue at most one task. The task itself never claims.
    Returns True when this call queued the task.
    """
    from .events import publish_topic_status
    from .tasks import generate_content_for_topic

    if not ensure_topic_content_ready(topic_id):
        return False
    try:
        generate_content_for_topic.delay(topic_id)
    except BrokerOperationalError:
        # Nothing will run; surface FAILED so the user can retry instead of
        # leaving the topic stuck in GENERATING.
        Topic.objects.filter(id=topic_id).update(
            content_status=Topic.ContentStatus.FAILED
        )
        publish_topic_status(topic_id)
        return False
    return True


def generate_topic_content(topic_id: int) -> None:
    topic = Topic.objects.get(id=topic_id)
    max_attempts = 2
    for attempt in range(max_attempts):
        response = generate_content(
            prompt=(
                f"Write a self-contained educational summary for the topic "
                f"'{topic.title}' as part of the subject '{topic.subject.name}'.\n\n"
                f"The summary should be 300-500 words and written for someone encountering "
                f"this topic for the first time. Focus on the core ideas, key terms, "
                f"and why this topic matters in the broader subject.\n\n"
                f"Also provide 2-5 carefully chosen external resource links. Prefer "
                f"Wikipedia for foundational overviews, reputable articles for depth, "
                f"and YouTube for visual explanations. Each link must point to a real, "
                f"accessible resource that is directly relevant to this specific topic."
            ),
            system_instruction=TOPIC_CONTENT_SYSTEM_INSTRUCTION,
            output_schema=TOPIC_CONTENT_SCHEMA,
            action="topic",
        )
        try:
            validated = _validate_topic_content(response)
            break
        except GenerationError:
            if attempt >= max_attempts - 1:
                raise
            continue
    Topic.objects.filter(id=topic_id).update(
        summary=validated["summary"],
        resource_links=validated["resource_links"],
    )


def review_topic_content(topic_id: int) -> bool:
    from apps.ai.services import review_content as ai_review

    topic = Topic.objects.get(id=topic_id)
    result = ai_review(
        content=topic.summary,
        criteria={"topic_title": topic.title, "subject_name": topic.subject.name},
    )
    return result["overall_passed"]


def ensure_buffer_ahead(topic: Topic) -> None:
    next_topics = Topic.objects.filter(
        subject=topic.subject,
        order__in=[topic.order + 1, topic.order + 2],
        content_status=Topic.ContentStatus.NOT_GENERATED,
    )
    for t in next_topics:
        request_topic_generation(t.id)


def generate_initial_batch(subject: Subject) -> None:
    first_topics = Topic.objects.filter(
        subject=subject,
        order__lte=3,
        content_status=Topic.ContentStatus.NOT_GENERATED,
    )
    for t in first_topics:
        request_topic_generation(t.id)


ROADMAP_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "level": {"type": "integer", "minimum": 1, "maximum": 3},
                    "description": {"type": "string"},
                },
                "required": ["title", "level", "description"],
            },
            "minItems": 20,
            "maxItems": 25,
        },
    },
    "required": ["topics"],
}


def _validate_roadmap_response(data: dict) -> list[dict]:
    if not isinstance(data, dict) or "topics" not in data:
        raise GenerationError(
            "Response missing 'topics' key", phase="roadmap_validation"
        )
    topics = data["topics"]
    if not isinstance(topics, list) or len(topics) < 20 or len(topics) > 25:
        raise GenerationError(
            f"Expected 20-25 topics, got {len(topics) if isinstance(topics, list) else 'invalid'}",
            phase="roadmap_validation",
        )
    validated = []
    for i, t in enumerate(topics):
        if not isinstance(t, dict):
            raise GenerationError(
                f"Topic {i} is not an object", phase="roadmap_validation"
            )
        if not isinstance(t.get("title"), str) or not t["title"].strip():
            raise GenerationError(
                f"Topic {i} missing valid title", phase="roadmap_validation"
            )
        if not isinstance(t.get("level"), int) or t["level"] not in (1, 2, 3):
            raise GenerationError(
                f"Topic {i} has invalid level", phase="roadmap_validation"
            )
        validated.append({"title": t["title"].strip(), "level": t["level"]})
    return validated


def generate_roadmap(subject: Subject, max_retries: int = 1) -> list[Topic]:
    prompt = (
        f"Design a 20-25 topic learning roadmap for the subject '{subject.name}'.\n\n"
        f"Structure it across 3 levels of depth:\n"
        f"  Level 1 (7-8 topics): Core foundations every beginner needs. "
        f"Start with the basics and build toward intermediate ground.\n"
        f"  Level 2 (7-8 topics): Deeper dives and practical application. "
        f"Assume the learner has completed Level 1.\n"
        f"  Level 3 (6-9 topics): Advanced concepts, edge cases, and current developments. "
        f"Push the learner toward mastery.\n\n"
        f"For each topic provide a title (concrete, 3-7 words), a level (1/2/3), "
        f"and a one-sentence description of what the learner will understand after completing it.\n\n"
        f"IMPORTANT: The full 20-25 topic sequence must be logically ordered — each topic "
        f"should build on concepts introduced in earlier topics."
    )
    for attempt in range(max_retries + 1):
        response = generate_content(
            prompt=prompt,
            system_instruction=ROADMAP_SYSTEM_INSTRUCTION,
            output_schema=ROADMAP_SCHEMA,
            action="roadmap",
        )
        try:
            validated = _validate_roadmap_response(response)
            break
        except GenerationError:
            if attempt >= max_retries:
                raise

    topics = [
        Topic(subject=subject, title=t["title"], level=t["level"], order=i + 1)
        for i, t in enumerate(validated)
    ]
    return Topic.objects.bulk_create(topics)


def append_roadmap_topics(subject: Subject, topics_data: list[dict]) -> list[Topic]:
    from django.db.models import Max

    max_order = (
        Topic.objects.filter(subject=subject).aggregate(max_order=Max("order"))[
            "max_order"
        ]
        or 0
    )

    topics = [
        Topic(
            subject=subject, title=t["title"], level=t["level"], order=max_order + i + 1
        )
        for i, t in enumerate(topics_data)
    ]
    return Topic.objects.bulk_create(topics)


TEMP_SHIFT = 10**9


def insert_roadmap_topics(
    subject: Subject,
    insert_before_order: int,
    topics_data: list[dict],
) -> list[Topic]:
    shift = len(topics_data)

    Topic.objects.filter(subject=subject, order__gte=insert_before_order).update(
        order=models.F("order") + TEMP_SHIFT
    )

    topics = [
        Topic(
            subject=subject,
            title=t["title"],
            level=t["level"],
            order=insert_before_order + i,
        )
        for i, t in enumerate(topics_data)
    ]
    created = Topic.objects.bulk_create(topics)

    Topic.objects.filter(
        subject=subject,
        order__gte=TEMP_SHIFT,
    ).update(order=models.F("order") - TEMP_SHIFT + shift)

    user_ids_past = list(
        TopicProgress.objects.filter(
            topic__subject=subject,
            status=TopicProgress.Status.PASSED,
            topic__order__gte=insert_before_order,
        )
        .values_list("user_id", flat=True)
        .distinct()
    )

    bonus_tps = []
    all_existing_tps = []
    for topic in created:
        existing_map = {
            tp.user_id: tp
            for tp in TopicProgress.objects.filter(
                user_id__in=user_ids_past, topic=topic
            )
        }
        for uid in user_ids_past:
            if uid in existing_map:
                if not existing_map[uid].is_bonus:
                    existing_map[uid].is_bonus = True
                    all_existing_tps.append(existing_map[uid])
            else:
                bonus_tps.append(TopicProgress(user_id=uid, topic=topic, is_bonus=True))

    if all_existing_tps:
        TopicProgress.objects.bulk_update(
            all_existing_tps,
            ["is_bonus"],
        )
    if bonus_tps:
        TopicProgress.objects.bulk_create(bonus_tps)

    return created


def update_roadmap_topic(topic_id: int, **fields) -> Topic:
    valid = {k: v for k, v in fields.items() if k in ("title", "level")}
    if valid:
        Topic.objects.filter(id=topic_id).update(**valid)
    return Topic.objects.get(id=topic_id)


def update_roadmap(
    subject: Subject,
    *,
    append: list[dict] | None = None,
    insert: dict | None = None,
    update: dict | None = None,
) -> dict:
    result = {"appended": [], "inserted": [], "updated": None}

    if update is not None:
        topic_id = update.pop("topic_id")
        result["updated"] = update_roadmap_topic(topic_id, **update)

    if insert is not None:
        result["inserted"] = insert_roadmap_topics(
            subject,
            insert_before_order=insert["before_order"],
            topics_data=insert["topics"],
        )

    if append is not None:
        result["appended"] = append_roadmap_topics(subject, append)

    return result


def set_notification_frequency(user, subject, hours: int):
    if not 1 <= hours <= 24:
        raise ValueError("Notification frequency must be between 1 and 24 hours")
    usp = UserSubjectProgress.objects.get(user=user, subject=subject)
    usp.notification_frequency_hours = hours
    usp.next_due_at = timezone.now() + timedelta(hours=hours)
    usp.save(update_fields=["notification_frequency_hours", "next_due_at"])
    return usp


def get_due_notifications():
    now = timezone.now()
    return UserSubjectProgress.objects.filter(
        status=UserSubjectProgress.Status.ACTIVE,
        next_due_at__isnull=False,
        next_due_at__lte=now,
    ).order_by("next_due_at")


def advance_due_time(usp, from_time):
    usp.next_due_at = from_time + timedelta(hours=usp.notification_frequency_hours)
    usp.save(update_fields=["next_due_at"])


def compute_level_threshold(topic_count: int) -> int:
    return max(1, min(math.ceil(topic_count * 0.8), topic_count - 1))


def get_subject_detail(user, subject_id: int) -> SubjectDetailResult:
    usp = UserSubjectProgress.objects.select_related("subject").get(
        user=user, subject_id=subject_id
    )

    topic_statuses = TopicProgress.objects.filter(
        user=user, topic=OuterRef("pk")
    ).values("status")[:1]
    completed_subq = TopicProgress.objects.filter(
        user=user, topic=OuterRef("pk")
    ).values("completed_at")[:1]
    viewed_subq = TopicProgress.objects.filter(
        user=user, topic=OuterRef("pk")
    ).values("resource_links_viewed_at")[:1]
    topics = (
        Topic.objects.filter(subject=usp.subject)
        .annotate(
            user_progress_status=Subquery(topic_statuses),
            user_completed_at=Subquery(completed_subq),
            user_links_viewed_at=Subquery(viewed_subq),
        )
        .order_by("level", "order")
    )

    level_groups = {}
    for t in topics:
        level_groups.setdefault(t.level, {"total": 0, "passed": 0})
        level_groups[t.level]["total"] += 1
        if t.user_progress_status in (
            TopicProgress.Status.PASSED,
            TopicProgress.Status.ADVANCED_PASSED,
        ):
            level_groups[t.level]["passed"] += 1

    levels = []
    for lv in (1, 2, 3):
        info = level_groups.get(lv, {"total": 0, "passed": 0})
        levels.append(
            {
                "level": lv,
                "total": info["total"],
                "passed": info["passed"],
                "threshold": compute_level_threshold(info["total"])
                if info["total"] > 0
                else 0,
                "is_unlocked": lv <= usp.level_unlocked,
            }
        )

    topics_data = []
    passed_statuses = (
        TopicProgress.Status.PASSED,
        TopicProgress.Status.ADVANCED_PASSED,
    )
    for t in topics:
        topics_data.append(
            {
                "id": t.id,
                "title": t.title,
                "level": t.level,
                "order": t.order,
                "content_status": t.content_status,
                "review_status": t.review_status,
                "user_progress_status": t.user_progress_status,
                "is_passed": t.user_progress_status in passed_statuses,
                "advanced_passed": t.user_progress_status
                == TopicProgress.Status.ADVANCED_PASSED,
                "completed_at": t.user_completed_at,
                "resource_links_viewed_at": t.user_links_viewed_at,
            }
        )

    return SubjectDetailResult(
        subject_id=usp.subject_id,
        name=usp.subject.name,
        status=usp.status,
        points=usp.points,
        level_unlocked=usp.level_unlocked,
        created_at=usp.created_at,
        notification_frequency_hours=usp.notification_frequency_hours,
        next_due_at=usp.next_due_at,
        roadmap_status=usp.subject.roadmap_status,
        percent_complete=(
            sum(t["is_passed"] for t in topics_data) * 100 // len(topics_data)
            if topics_data else 0
        ),
        levels=levels,
        topics=topics_data,
    )


@dataclass
class LevelCheckResult:
    action: Literal["none", "level_up", "subject_completed"]
    new_level_unlocked: int | None = None
    suggestions: list[dict] | None = None
    slots_available: int = 0


def _generate_subject_suggestions(user, count: int = 5) -> list[dict]:
    enrolled_ids = list(
        UserSubjectProgress.objects.filter(user=user).values_list(
            "subject_id", flat=True
        )
    )
    interest_ids = list(
        UserInterest.objects.filter(user=user)
        .exclude(subject_id__in=enrolled_ids)
        .values_list("subject_id", flat=True)
    )

    popular = (
        Subject.objects.exclude(id__in=enrolled_ids)
        .filter(
            topics__isnull=False,
        )
        .annotate(
            enrollment_count=Count(
                "user_progress",
                filter=Q(user_progress__status=UserSubjectProgress.Status.ACTIVE),
            )
        )
        .order_by("-enrollment_count")
    )

    suggestions = []
    seen_ids = set()

    for sid in interest_ids:
        s = Subject.objects.filter(id=sid).first()
        if s and s.id not in seen_ids:
            suggestions.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "reason": "You marked interest in this subject",
                }
            )
            seen_ids.add(s.id)

    for s in popular:
        if len(suggestions) >= count:
            break
        if s.id not in seen_ids:
            suggestions.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "reason": f"Popular subject ({s.enrollment_count} learners)",
                }
            )
            seen_ids.add(s.id)

    return suggestions


def check_level_progress(user, subject) -> LevelCheckResult:
    usp = UserSubjectProgress.objects.get(user=user, subject=subject)
    current_level = usp.level_unlocked
    level_topics = Topic.objects.filter(subject=subject, level=current_level)
    total = level_topics.count()
    if total == 0:
        # Roadmap not generated yet (or failed); there is nothing to complete.
        return LevelCheckResult(action="none")
    completed = TopicProgress.objects.filter(
        user=user,
        topic__subject=subject,
        topic__level=current_level,
        status=TopicProgress.Status.PASSED,
    ).count()
    threshold = compute_level_threshold(total)

    if completed < threshold:
        return LevelCheckResult(action="none")

    if current_level < 3:
        usp.level_unlocked = current_level + 1
        usp.save(update_fields=["level_unlocked"])
        slots = get_available_slots(user)
        suggestions = _generate_subject_suggestions(user)
        return LevelCheckResult(
            action="level_up",
            new_level_unlocked=current_level + 1,
            slots_available=slots,
            suggestions=suggestions,
        )

    usp.status = UserSubjectProgress.Status.COMPLETED
    usp.needs_subject_selection = True
    usp.selection_pending_since = timezone.now()
    usp.save(
        update_fields=["status", "needs_subject_selection", "selection_pending_since"]
    )
    suggestions = _generate_subject_suggestions(user)
    return LevelCheckResult(
        action="subject_completed",
        suggestions=suggestions,
    )


def get_available_slots(user) -> int:
    active_count = UserSubjectProgress.objects.filter(
        user=user,
        status=UserSubjectProgress.Status.ACTIVE,
    ).count()
    return max(0, 5 - active_count)


def claim_roadmap_generation(subject_id: int) -> bool:
    """Atomically move a subject to GENERATING; True only for the caller that won."""
    claimed = Subject.objects.filter(
        id=subject_id,
        roadmap_status__in=[
            Subject.RoadmapStatus.NOT_GENERATED,
            Subject.RoadmapStatus.FAILED,
        ],
    ).update(
        roadmap_status=Subject.RoadmapStatus.GENERATING,
        roadmap_started_at=timezone.now(),
    )
    return claimed > 0


def set_roadmap_status(subject_id: int, status: str) -> None:
    Subject.objects.filter(id=subject_id).update(roadmap_status=status)


def request_roadmap_generation(subject: Subject) -> bool:
    """Start background roadmap generation unless it is already running.

    Returns True when this call started it. Safe to call repeatedly.
    """
    from .events import publish_roadmap_status
    from .tasks import generate_roadmap_for_subject

    if not claim_roadmap_generation(subject.id):
        subject.refresh_from_db(fields=["roadmap_status"])
        return False
    try:
        generate_roadmap_for_subject.delay(subject.id)
    except BrokerOperationalError:
        # Nothing will run; surface FAILED so the user can retry instead of
        # leaving the subject stuck in GENERATING.
        set_roadmap_status(subject.id, Subject.RoadmapStatus.FAILED)
        publish_roadmap_status(subject)
        return False
    subject.refresh_from_db(fields=["roadmap_status"])
    return True


def build_roadmap(subject_id: int) -> Subject:
    """Generate and save the roadmap unless topics already exist (idempotent)."""
    subject = Subject.objects.get(id=subject_id)
    if not Topic.objects.filter(subject=subject).exists():
        generate_roadmap(subject)
    return subject


def retry_roadmap(user, subject_id: int) -> Subject:
    """Re-run roadmap generation for a subject the user is enrolled in."""
    usp = UserSubjectProgress.objects.select_related("subject").get(
        user=user, subject_id=subject_id
    )
    subject = usp.subject
    if not Topic.objects.filter(subject=subject).exists():
        request_roadmap_generation(subject)
    return subject


def add_subject_to_user(user, subject) -> UserSubjectProgress:
    existing = UserSubjectProgress.objects.filter(user=user, subject=subject).first()
    if existing is not None:
        return existing

    if get_available_slots(user) == 0:
        raise ValueError(SUBJECT_LIMIT_MESSAGE)

    usp = UserSubjectProgress.objects.create(user=user, subject=subject)

    # Roadmap generation is slow, so it runs in the background; the user is
    # enrolled right away and follows progress via roadmap_status / SSE.
    if not Topic.objects.filter(subject=subject).exists():
        request_roadmap_generation(subject)

    UserSubjectProgress.objects.filter(
        user=user,
        needs_subject_selection=True,
    ).update(needs_subject_selection=False, selection_pending_since=None)

    return usp


def explore_subjects(
    user, limit: int | None = None, interested_only: bool = False
) -> list[dict]:
    """Subjects the user is not enrolled in (active or completed), most popular first.

    interested_only keeps just the starred ones, filtered before the limit slice.
    """
    interest_ids = set(
        UserInterest.objects.filter(user=user).values_list("subject_id", flat=True)
    )

    # distinct=True: both counts span joins, so without it they multiply each other.
    subjects = (
        Subject.objects.exclude(user_progress__user=user)
        .annotate(
            topic_count=Count("topics", distinct=True),
            enrollment_count=Count(
                "user_progress",
                filter=Q(user_progress__status=UserSubjectProgress.Status.ACTIVE),
                distinct=True,
            ),
        )
        .filter(topic_count__gt=0)
        .order_by("-enrollment_count", "name")
    )
    if interested_only:
        subjects = subjects.filter(id__in=interest_ids)

    return [
        {
            "id": s.id,
            "name": s.name,
            "topic_count": s.topic_count,
            "enrollment_count": s.enrollment_count,
            "is_interested": s.id in interest_ids,
        }
        for s in subjects[:limit]
    ]


def get_subject_preview(user, subject_id: int) -> SubjectPreviewResult:
    subject = Subject.objects.get(id=subject_id)

    enrolled_ids = set(
        UserSubjectProgress.objects.filter(user=user).values_list(
            "subject_id", flat=True
        )
    )
    completed_ids = set(
        UserSubjectProgress.objects.filter(
            user=user,
            status=UserSubjectProgress.Status.COMPLETED,
        ).values_list("subject_id", flat=True)
    )
    interest_ids = set(
        UserInterest.objects.filter(user=user).values_list("subject_id", flat=True)
    )
    enrollment_count = UserSubjectProgress.objects.filter(
        subject=subject,
        status=UserSubjectProgress.Status.ACTIVE,
    ).count()

    topics = Topic.objects.filter(subject=subject).order_by("level", "order")

    level_counts = {}
    for t in topics:
        level_counts.setdefault(t.level, 0)
        level_counts[t.level] += 1

    levels = []
    for lv in (1, 2, 3):
        levels.append(
            {
                "level": lv,
                "topic_count": level_counts.get(lv, 0),
            }
        )

    topics_data = [
        {
            "id": t.id,
            "title": t.title,
            "level": t.level,
            "order": t.order,
        }
        for t in topics
    ]

    return SubjectPreviewResult(
        subject_id=subject.id,
        name=subject.name,
        enrollment_count=enrollment_count,
        is_enrolled=subject.id in enrolled_ids,
        is_completed=subject.id in completed_ids,
        is_interested=subject.id in interest_ids,
        levels=levels,
        topics=topics_data,
    )


def mark_subject_interest(user, subject) -> UserInterest:
    interest, _ = UserInterest.objects.get_or_create(user=user, subject=subject)
    return interest


def remove_subject_interest(user, subject) -> None:
    UserInterest.objects.filter(user=user, subject=subject).delete()


def check_needs_selection(user) -> dict | None:
    usp = UserSubjectProgress.objects.filter(
        user=user,
        needs_subject_selection=True,
    ).first()
    if usp is None:
        return None
    return {
        "subject_id": usp.subject_id,
        "subject_name": usp.subject.name,
        "suggestions": _generate_subject_suggestions(user),
    }


QUIZ_SYSTEM_INSTRUCTION = (
    "You are an educational assessment designer creating multiple-choice quiz questions. "
    "Your questions test genuine understanding, not rote memorization. "
    "Every question should require the learner to apply, analyze, or evaluate — "
    "not just recall a fact. All four options must be plausible to someone who "
    "hasn't studied the material. Distractors should reflect common misconceptions. "
    "Explanations should teach, not just identify the right answer. "
    "Each option string must contain only the option text — do NOT prefix options "
    "with letters like 'a)', 'b)', 'A.', etc. The frontend renders its own labels."
)

_OPTION_LETTER_PREFIX_RE = re.compile(r"^[a-dA-D][).\s]+")


def _strip_option_prefix(opt: str) -> str:
    return _OPTION_LETTER_PREFIX_RE.sub("", opt).strip()


QUIZ_QUESTION_ITEM = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 4,
            "maxItems": 4,
        },
        "correct_index": {"type": "integer", "minimum": 0, "maximum": 3},
        "explanation": {"type": "string"},
    },
    "required": ["question", "options", "correct_index", "explanation"],
}

NORMAL_QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": QUIZ_QUESTION_ITEM,
            "minItems": 4,
            "maxItems": 6,
        },
        "total_points": {"type": "integer", "minimum": 90, "maximum": 110},
    },
    "required": ["questions", "total_points"],
}

ADVANCED_QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": QUIZ_QUESTION_ITEM,
            "minItems": 9,
            "maxItems": 12,
        },
        "total_points": {"type": "integer", "minimum": 180, "maximum": 220},
    },
    "required": ["questions", "total_points"],
}

PASS_THRESHOLD = 0.6
RETRY_COOLDOWN_HOURS = 1


def _validate_quiz_response(data: dict, quiz_type: str) -> dict:
    if not isinstance(data, dict):
        raise GenerationError("Response is not a dict", phase="quiz_validation")
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise GenerationError("Missing 'questions' array", phase="quiz_validation")
    if quiz_type == QuizAttempt.QuizType.NORMAL:
        if len(questions) < 4 or len(questions) > 6:
            raise GenerationError(
                f"Expected 4-6 questions for normal quiz, got {len(questions)}",
                phase="quiz_validation",
            )
        tp = data.get("total_points", 0)
        if not isinstance(tp, int) or tp < 90 or tp > 110:
            raise GenerationError(
                f"Expected total_points 90-110 for normal quiz, got {tp}",
                phase="quiz_validation",
            )
    else:
        if len(questions) < 9 or len(questions) > 12:
            raise GenerationError(
                f"Expected 9-12 questions for advanced quiz, got {len(questions)}",
                phase="quiz_validation",
            )
        tp = data.get("total_points", 0)
        if not isinstance(tp, int) or tp < 180 or tp > 220:
            raise GenerationError(
                f"Expected total_points 180-220 for advanced quiz, got {tp}",
                phase="quiz_validation",
            )
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            raise GenerationError(
                f"Question {i} is not a dict", phase="quiz_validation"
            )
        if not isinstance(q.get("question"), str) or not q["question"].strip():
            raise GenerationError(f"Question {i} missing text", phase="quiz_validation")
        opts = q.get("options")
        if (
            not isinstance(opts, list)
            or len(opts) != 4
            or not all(isinstance(o, str) and o.strip() for o in opts)
        ):
            raise GenerationError(
                f"Question {i} missing valid options", phase="quiz_validation"
            )
        opts = [_strip_option_prefix(o) for o in opts]
        if not all(o for o in opts):
            raise GenerationError(
                f"Question {i} options empty after stripping prefix",
                phase="quiz_validation",
            )
        q["options"] = opts
        ci = q.get("correct_index")
        if not isinstance(ci, int) or ci < 0 or ci > 3:
            raise GenerationError(
                f"Question {i} invalid correct_index", phase="quiz_validation"
            )
        if not isinstance(q.get("explanation"), str) or not q["explanation"].strip():
            raise GenerationError(
                f"Question {i} missing explanation", phase="quiz_validation"
            )
    return data


def _next_attempt_number(user, topic, quiz_type: str) -> int:
    last = (
        QuizAttempt.objects.filter(
            user=user,
            topic=topic,
            quiz_type=quiz_type,
        )
        .order_by("-attempt_number")
        .values_list("attempt_number", flat=True)
        .first()
    )
    return (last or 0) + 1


def global_leaderboard(limit=50):
    User = get_user_model()

    all_users = (
        User.objects.select_related("preferences")
        .annotate(
            total_points=Coalesce(Sum("subject_progress__points"), Value(0)),
        )
        .order_by("-total_points", "id")
    )

    result = []
    for rank, user in enumerate(all_users, 1):
        if user.preferences.leaderboard_visible:
            result.append(
                {
                    "rank": rank,
                    "user_id": user.id,
                    "display_name": user.display_name or user.email,
                    "total_points": user.total_points,
                }
            )
            if len(result) >= limit:
                break

    return result


def subject_leaderboard(subject_id, limit=20):
    progress_qs = (
        UserSubjectProgress.objects.filter(subject_id=subject_id, points__gt=0)
        .select_related("user__preferences")
        .order_by("-points", "user_id")
    )

    result = []
    for rank, usp in enumerate(progress_qs, 1):
        if usp.user.preferences.leaderboard_visible:
            result.append(
                {
                    "rank": rank,
                    "user_id": usp.user_id,
                    "display_name": usp.user.display_name or usp.user.email,
                    "points": usp.points,
                }
            )
            if len(result) >= limit:
                break

    return result


def topic_leaderboard(topic_id, limit=20):
    progress_qs = (
        TopicProgress.objects.filter(
            topic_id=topic_id,
            points__gt=0,
        )
        .select_related("user__preferences")
        .order_by("-points", "user_id")
    )

    result = []
    for rank, tp in enumerate(progress_qs, 1):
        if tp.user.preferences.leaderboard_visible:
            result.append(
                {
                    "rank": rank,
                    "user_id": tp.user_id,
                    "display_name": tp.user.display_name or tp.user.email,
                    "points": tp.points,
                }
            )
            if len(result) >= limit:
                break

    return result


def others_learning(topic_id, viewing_user, limit=10):
    topic = Topic.objects.get(id=topic_id)
    viewing_usp = UserSubjectProgress.objects.filter(
        user=viewing_user,
        subject=topic.subject,
    ).first()
    viewing_level = viewing_usp.level_unlocked if viewing_usp else 1

    other_progress = (
        TopicProgress.objects.filter(
            topic=topic,
        )
        .exclude(
            user=viewing_user,
        )
        .select_related("user")
        .filter(
            user__preferences__others_learning_visible=True,
        )
    )

    entries = []
    for tp in other_progress:
        usp = UserSubjectProgress.objects.filter(
            user=tp.user,
            subject=topic.subject,
        ).first()
        if usp is None:
            continue

        is_active = usp.status == UserSubjectProgress.Status.ACTIVE
        is_similar_level = abs(usp.level_unlocked - viewing_level) <= 1
        is_completed = tp.status in (
            TopicProgress.Status.PASSED,
            TopicProgress.Status.ADVANCED_PASSED,
        )

        if is_active and is_similar_level:
            priority = 0
        elif is_completed:
            priority = 1
        else:
            continue

        entries.append(
            {
                "priority": priority,
                "user_id": tp.user_id,
                "display_name": tp.user.display_name or tp.user.email,
                "status": usp.status,
                "level": tp.topic.level,
            }
        )

    entries.sort(key=lambda x: (x["priority"], x["user_id"]))
    for e in entries:
        del e["priority"]
    return entries[:limit]


def _earn_points(user, topic, points: int) -> None:
    TopicProgress.objects.filter(user=user, topic=topic).update(
        points=models.F("points") + points,
    )
    UserSubjectProgress.objects.filter(user=user, subject=topic.subject).update(
        points=models.F("points") + points,
    )


def get_accessible_topic(user, topic_id: int) -> Topic:
    """The topic, if the user is enrolled in its subject and has unlocked its level.

    Not enrolled raises Topic.DoesNotExist (404); a locked level raises 403.
    """
    topic = Topic.objects.select_related("subject").get(id=topic_id)
    usp = UserSubjectProgress.objects.filter(
        user=user, subject_id=topic.subject_id
    ).first()
    if usp is None:
        raise Topic.DoesNotExist
    if topic.level > usp.level_unlocked:
        raise PermissionDenied("This level is locked.")
    return topic


def prefetch_topic_content(topic: Topic) -> None:
    """Start generating this topic and the next two if they have no content yet.

    Safe on every page open: request_topic_generation only queues a task for a
    topic it wins the claim on, so refreshes cost one UPDATE and queue nothing.
    FAILED topics are not restarted here; that is the retry endpoint's job.
    """
    request_topic_generation(topic.id)
    ensure_buffer_ahead(topic)
    topic.refresh_from_db(fields=["content_status"])


def retry_topic(user, topic_id: int) -> Topic:
    """Restart generation for a FAILED topic. A no-op while it is running or ready."""
    topic = get_accessible_topic(user, topic_id)
    Topic.objects.filter(id=topic.id, content_status=Topic.ContentStatus.FAILED).update(
        content_status=Topic.ContentStatus.NOT_GENERATED,
        review_status=Topic.ReviewStatus.PENDING,
        review_attempts=0,
        generation_started_at=None,
    )
    request_topic_generation(topic.id)
    topic.refresh_from_db(fields=["content_status"])
    return topic


def get_topic_progress(user, topic: Topic) -> dict:
    tp = TopicProgress.objects.filter(user=user, topic=topic).first()
    status = tp.status if tp else TopicProgress.Status.NOT_STARTED
    next_topic = (
        Topic.objects.filter(subject_id=topic.subject_id, order__gt=topic.order)
        .order_by("order")
        .values("id", "title")
        .first()
    )
    return {
        "status": status,
        "is_passed": status
        in (TopicProgress.Status.PASSED, TopicProgress.Status.ADVANCED_PASSED),
        "advanced_passed": status == TopicProgress.Status.ADVANCED_PASSED,
        "completed_at": tp.completed_at if tp else None,
        "resource_links_viewed_at": tp.resource_links_viewed_at if tp else None,
        "advanced_quiz_available": can_take_advanced_quiz(user, topic),
        "next_topic": next_topic,
    }


def get_own_quiz_attempt(user, topic, attempt_id) -> QuizAttempt:
    return QuizAttempt.objects.get(id=attempt_id, user=user, topic=topic)


def mark_resource_links_viewed(user, topic) -> TopicProgress:
    tp, _ = TopicProgress.objects.get_or_create(user=user, topic=topic)
    tp.resource_links_viewed_at = timezone.now()
    tp.save(update_fields=["resource_links_viewed_at"])
    return tp


def can_take_advanced_quiz(user, topic) -> bool:
    tp = TopicProgress.objects.filter(user=user, topic=topic).first()
    if tp is None:
        return False
    if tp.status not in (
        TopicProgress.Status.PASSED,
        TopicProgress.Status.ADVANCED_PASSED,
    ):
        return False
    if tp.resource_links_viewed_at is None:
        return False
    if tp.status == TopicProgress.Status.ADVANCED_PASSED:
        return False
    return True


def generate_quiz(
    user,
    topic,
    quiz_type: str,
    prior_missed_questions: list[str] | None = None,
) -> QuizAttempt:
    quiz_type = quiz_type.upper()
    tp, _ = TopicProgress.objects.get_or_create(user=user, topic=topic)

    if quiz_type == QuizAttempt.QuizType.ADVANCED:
        if not can_take_advanced_quiz(user, topic):
            raise ValueError(
                "Advanced quiz not available: pass the normal quiz and engage with resource links first"
            )
    else:
        if tp.status == TopicProgress.Status.PASSED:
            raise ValueError("Normal quiz already passed for this topic")

    last_attempt_time = (
        QuizAttempt.objects.filter(
            user=user,
            topic=topic,
            quiz_type=quiz_type,
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if last_attempt_time is not None:
        cooldown_end = last_attempt_time + timedelta(hours=RETRY_COOLDOWN_HOURS)
        if timezone.now() < cooldown_end:
            remaining = (cooldown_end - timezone.now()).seconds // 60
            raise ValueError(
                f"Please wait {remaining} minutes before retaking this quiz"
            )

    schema = (
        NORMAL_QUIZ_SCHEMA
        if quiz_type == QuizAttempt.QuizType.NORMAL
        else ADVANCED_QUIZ_SCHEMA
    )
    difficulty_note = (
        "Make the questions at least twice as difficult as a normal quiz — "
        "this is an advanced quiz for learners who have studied the resource links."
        if quiz_type == QuizAttempt.QuizType.ADVANCED
        else ""
    )
    missed_context = ""
    if prior_missed_questions:
        missed_context = (
            "\n\nThe learner previously missed these questions. Focus extra attention "
            "on these concepts:\n" + "\n".join(f"- {m}" for m in prior_missed_questions)
        )

    prompt = (
        f"Create a {quiz_type.lower()} quiz for the topic '{topic.title}' "
        f"(subject: '{topic.subject.name}').\n\n"
        f"Reference material:\n{topic.summary}\n\n"
        f"Question requirements:\n"
        f"- 4 multiple-choice options per question, plain text only — no "
        f"'a)', 'b)', 'A.', or other letter prefixes in the option strings "
        f"(the frontend renders its own labels)\n"
        f"- Provide the index (0-3) of the single correct answer\n"
        f"- Write a brief explanation of why the correct answer is right and why "
        f"the distractors are wrong\n"
        f"- All options must be plausible — distractors should target common misconceptions\n"
        f"- Questions should test understanding and application, not recall of trivia\n"
        f"{difficulty_note}{missed_context}"
    )

    max_attempts = 2
    for attempt in range(max_attempts):
        response = generate_content(
            prompt=prompt,
            system_instruction=QUIZ_SYSTEM_INSTRUCTION,
            output_schema=schema,
            action=(
                "quiz_advanced"
                if quiz_type == QuizAttempt.QuizType.ADVANCED
                else "quiz_normal"
            ),
        )
        try:
            validated = _validate_quiz_response(response, quiz_type)
            break
        except (GenerationError, ProviderError):
            if attempt >= max_attempts - 1:
                raise

    attempt_number = _next_attempt_number(user, topic, quiz_type)

    attempt_obj = QuizAttempt.objects.create(
        user=user,
        topic=topic,
        quiz_type=quiz_type,
        attempt_number=attempt_number,
        questions=validated["questions"],
        total_points=validated["total_points"],
    )

    return attempt_obj


def submit_quiz(attempt_id: int, answers: list[int]) -> QuizAttempt:
    attempt = QuizAttempt.objects.select_related("topic", "user").get(id=attempt_id)

    if attempt.answers is not None:
        raise ValueError("Quiz already submitted")

    questions = attempt.questions
    if len(answers) != len(questions):
        raise ValueError(f"Expected {len(questions)} answers, got {len(answers)}")

    correct_count = 0
    for i, (q, answer) in enumerate(zip(questions, answers)):
        if not isinstance(answer, int) or answer < 0 or answer > 3:
            raise ValueError(f"Answer {i} is not a valid option index")
        if answer == q["correct_index"]:
            correct_count += 1

    ratio = correct_count / len(questions) if questions else 0
    passed = ratio >= PASS_THRESHOLD
    earned_points = int(attempt.total_points * ratio) if passed else 0

    attempt.answers = answers
    attempt.score = earned_points
    attempt.passed = passed
    attempt.save(update_fields=["answers", "score", "passed"])

    if attempt.quiz_type == QuizAttempt.QuizType.NORMAL:
        TopicProgress.objects.filter(user=attempt.user, topic=attempt.topic).update(
            normal_quiz_attempts=models.F("normal_quiz_attempts") + 1,
        )
    else:
        TopicProgress.objects.filter(user=attempt.user, topic=attempt.topic).update(
            advanced_quiz_attempts=models.F("advanced_quiz_attempts") + 1,
        )

    if passed:
        topic = attempt.topic
        tp, _ = TopicProgress.objects.get_or_create(user=attempt.user, topic=topic)
        _earn_points(attempt.user, topic, earned_points)

        if attempt.quiz_type == QuizAttempt.QuizType.NORMAL:
            if tp.status not in (
                TopicProgress.Status.PASSED,
                TopicProgress.Status.ADVANCED_PASSED,
            ):
                tp.status = TopicProgress.Status.PASSED
                tp.completed_at = timezone.now()
                tp.save(update_fields=["status", "completed_at"])
        else:
            tp.status = TopicProgress.Status.ADVANCED_PASSED
            tp.save(update_fields=["status"])

    return attempt
