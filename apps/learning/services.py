import math
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from django.contrib.auth import get_user_model
from django.db import connection, models, transaction
from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.ai.services import generate_content, generate_embedding, rank_or_resolve, standardize_subject_name
from apps.ai.exceptions import ProviderError
from .exceptions import GenerationError
from .models import Subject, Topic, TopicProgress, UserInterest, UserSubjectProgress, QuizAttempt


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


def _compute_and_store_embedding(subject: Subject, text: str) -> None:
    if connection.vendor != "postgresql":
        return
    embedding = generate_embedding(text)
    Subject.objects.filter(id=subject.id).update(embedding=embedding)


def _find_similar_subjects(query_vector: list[float]) -> list[dict]:
    if connection.vendor != "postgresql":
        return []

    from pgvector.django import CosineDistance

    similar = Subject.objects.filter(
        embedding__isnull=False,
    ).annotate(
        distance=CosineDistance("embedding", query_vector)
    ).filter(
        distance__lte=0.15
    ).order_by("distance")[:5]

    return [
        {"id": str(s.id), "name": s.name, "similarity_score": float(1 - s.distance)}
        for s in similar
    ]


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
        with transaction.atomic():
            subject = Subject.objects.create(name=standardized_name)
            _compute_and_store_embedding(subject, standardized_name)
        return ResolveResult(action="resolve", subject=subject)

    result = rank_or_resolve(raw_input, candidates)
    action = result.get("action")

    if action == "resolve":
        subject = Subject.objects.get(id=result["subject_id"])
        return ResolveResult(action="resolve", subject=subject)

    if action == "create":
        standardized_name = result["standardized_name"]
        with transaction.atomic():
            subject = Subject.objects.create(name=standardized_name)
            _compute_and_store_embedding(subject, standardized_name)
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
                    "type": {"type": "string", "enum": ["article", "video", "wikipedia"]},
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
        raise GenerationError("Response is not a dict", phase="topic_content_validation")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise GenerationError("Missing or empty 'summary'", phase="topic_content_validation")
    links = data.get("resource_links")
    if not isinstance(links, list) or len(links) < 2 or len(links) > 5:
        raise GenerationError(
            f"Expected 2-5 resource_links, got {len(links) if isinstance(links, list) else 'invalid'}",
            phase="topic_content_validation",
        )
    for i, link in enumerate(links):
        if not isinstance(link, dict):
            raise GenerationError(f"Resource link {i} is not a dict", phase="topic_content_validation")
        if not isinstance(link.get("title"), str) or not link["title"].strip():
            raise GenerationError(f"Resource link {i} missing valid title", phase="topic_content_validation")
        if not isinstance(link.get("url"), str) or not link["url"].strip():
            raise GenerationError(f"Resource link {i} missing valid url", phase="topic_content_validation")
        if link.get("type") not in _VALID_TYPES:
            raise GenerationError(
                f"Resource link {i} invalid type '{link.get('type')}'", phase="topic_content_validation"
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
    return result["passed"]


def ensure_buffer_ahead(topic: Topic) -> None:
    from .tasks import generate_content_for_topic
    next_topics = Topic.objects.filter(
        subject=topic.subject,
        order__in=[topic.order + 1, topic.order + 2],
    )
    for t in next_topics:
        if t.content_status == Topic.ContentStatus.NOT_GENERATED:
            generate_content_for_topic.delay(t.id)


def generate_initial_batch(subject: Subject) -> None:
    from .tasks import generate_content_for_topic
    first_topics = Topic.objects.filter(
        subject=subject,
        order__lte=3,
        content_status=Topic.ContentStatus.NOT_GENERATED,
    )
    for t in first_topics:
        generate_content_for_topic.delay(t.id)


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
        raise GenerationError("Response missing 'topics' key", phase="roadmap_validation")
    topics = data["topics"]
    if not isinstance(topics, list) or len(topics) < 20 or len(topics) > 25:
        raise GenerationError(
            f"Expected 20-25 topics, got {len(topics) if isinstance(topics, list) else 'invalid'}",
            phase="roadmap_validation",
        )
    validated = []
    for i, t in enumerate(topics):
        if not isinstance(t, dict):
            raise GenerationError(f"Topic {i} is not an object", phase="roadmap_validation")
        if not isinstance(t.get("title"), str) or not t["title"].strip():
            raise GenerationError(f"Topic {i} missing valid title", phase="roadmap_validation")
        if not isinstance(t.get("level"), int) or t["level"] not in (1, 2, 3):
            raise GenerationError(f"Topic {i} has invalid level", phase="roadmap_validation")
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

    max_order = Topic.objects.filter(subject=subject).aggregate(
        max_order=Max("order")
    )["max_order"] or 0

    topics = [
        Topic(subject=subject, title=t["title"], level=t["level"], order=max_order + i + 1)
        for i, t in enumerate(topics_data)
    ]
    return Topic.objects.bulk_create(topics)


TEMP_SHIFT = 10 ** 9


def insert_roadmap_topics(
    subject: Subject,
    insert_before_order: int,
    topics_data: list[dict],
) -> list[Topic]:
    shift = len(topics_data)

    Topic.objects.filter(
        subject=subject, order__gte=insert_before_order
    ).update(order=models.F("order") + TEMP_SHIFT)

    topics = [
        Topic(subject=subject, title=t["title"], level=t["level"], order=insert_before_order + i)
        for i, t in enumerate(topics_data)
    ]
    created = Topic.objects.bulk_create(topics)

    Topic.objects.filter(
        subject=subject, order__gte=TEMP_SHIFT,
    ).update(order=models.F("order") - TEMP_SHIFT + shift)

    user_ids_past = list(
        TopicProgress.objects.filter(
            topic__subject=subject,
            status=TopicProgress.Status.PASSED,
            topic__order__gte=insert_before_order,
        ).values_list("user_id", flat=True).distinct()
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


@dataclass
class LevelCheckResult:
    action: Literal["none", "level_up", "subject_completed"]
    new_level_unlocked: int | None = None
    suggestions: list[dict] | None = None
    slots_available: int = 0


def _generate_subject_suggestions(user, count: int = 5) -> list[dict]:
    enrolled_ids = list(
        UserSubjectProgress.objects.filter(user=user).values_list("subject_id", flat=True)
    )
    interest_ids = list(
        UserInterest.objects.filter(user=user).exclude(subject_id__in=enrolled_ids)
        .values_list("subject_id", flat=True)
    )

    popular = Subject.objects.exclude(id__in=enrolled_ids).filter(
        topics__isnull=False,
    ).annotate(
        enrollment_count=Count("user_progress", filter=Q(user_progress__status=UserSubjectProgress.Status.ACTIVE))
    ).order_by("-enrollment_count")

    suggestions = []
    seen_ids = set()

    for sid in interest_ids:
        s = Subject.objects.filter(id=sid).first()
        if s and s.id not in seen_ids:
            suggestions.append({"id": s.id, "name": s.name, "reason": "You marked interest in this subject"})
            seen_ids.add(s.id)

    for s in popular:
        if len(suggestions) >= count:
            break
        if s.id not in seen_ids:
            suggestions.append({"id": s.id, "name": s.name, "reason": f"Popular subject ({s.enrollment_count} learners)"})
            seen_ids.add(s.id)

    return suggestions


def check_level_progress(user, subject) -> LevelCheckResult:
    usp = UserSubjectProgress.objects.get(user=user, subject=subject)
    current_level = usp.level_unlocked
    level_topics = Topic.objects.filter(subject=subject, level=current_level)
    total = level_topics.count()
    completed = TopicProgress.objects.filter(
        user=user, topic__subject=subject, topic__level=current_level,
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
    usp.save(update_fields=["status", "needs_subject_selection", "selection_pending_since"])
    suggestions = _generate_subject_suggestions(user)
    return LevelCheckResult(
        action="subject_completed",
        suggestions=suggestions,
    )


def get_available_slots(user) -> int:
    active_count = UserSubjectProgress.objects.filter(
        user=user, status=UserSubjectProgress.Status.ACTIVE,
    ).count()
    return max(0, 5 - active_count)


def add_subject_to_user(user, subject) -> UserSubjectProgress:
    existing = UserSubjectProgress.objects.filter(user=user, subject=subject).first()
    if existing is not None:
        return existing

    if get_available_slots(user) == 0:
        raise ValueError("You've reached the limit of 5 active subjects. Remove one or complete a subject to add a new one.")

    if not Topic.objects.filter(subject=subject).exists():
        generate_roadmap(subject)
        generate_initial_batch(subject)

    usp = UserSubjectProgress.objects.create(user=user, subject=subject)

    UserSubjectProgress.objects.filter(
        user=user, needs_subject_selection=True,
    ).update(needs_subject_selection=False, selection_pending_since=None)

    return usp


def explore_subjects(user) -> list[dict]:
    enrolled_ids = set(
        UserSubjectProgress.objects.filter(user=user).values_list("subject_id", flat=True)
    )
    completed_ids = set(
        UserSubjectProgress.objects.filter(
            user=user, status=UserSubjectProgress.Status.COMPLETED,
        ).values_list("subject_id", flat=True)
    )
    interest_ids = set(
        UserInterest.objects.filter(user=user).values_list("subject_id", flat=True)
    )

    subjects = Subject.objects.filter(topics__isnull=False).annotate(
        enrollment_count=Count("user_progress", filter=Q(user_progress__status=UserSubjectProgress.Status.ACTIVE)),
    ).order_by("-enrollment_count", "name")

    return [
        {
            "id": s.id,
            "name": s.name,
            "enrollment_count": s.enrollment_count,
            "is_enrolled": s.id in enrolled_ids,
            "is_completed": s.id in completed_ids,
            "is_interested": s.id in interest_ids,
        }
        for s in subjects
    ]


def mark_subject_interest(user, subject) -> UserInterest:
    interest, _ = UserInterest.objects.get_or_create(user=user, subject=subject)
    return interest


def remove_subject_interest(user, subject) -> None:
    UserInterest.objects.filter(user=user, subject=subject).delete()


def check_needs_selection(user) -> dict | None:
    usp = UserSubjectProgress.objects.filter(
        user=user, needs_subject_selection=True,
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
            raise GenerationError(f"Question {i} is not a dict", phase="quiz_validation")
        if not isinstance(q.get("question"), str) or not q["question"].strip():
            raise GenerationError(f"Question {i} missing text", phase="quiz_validation")
        opts = q.get("options")
        if not isinstance(opts, list) or len(opts) != 4 or not all(isinstance(o, str) and o.strip() for o in opts):
            raise GenerationError(f"Question {i} missing valid options", phase="quiz_validation")
        opts = [_strip_option_prefix(o) for o in opts]
        if not all(o for o in opts):
            raise GenerationError(f"Question {i} options empty after stripping prefix", phase="quiz_validation")
        q["options"] = opts
        ci = q.get("correct_index")
        if not isinstance(ci, int) or ci < 0 or ci > 3:
            raise GenerationError(f"Question {i} invalid correct_index", phase="quiz_validation")
        if not isinstance(q.get("explanation"), str) or not q["explanation"].strip():
            raise GenerationError(f"Question {i} missing explanation", phase="quiz_validation")
    return data


def _next_attempt_number(user, topic, quiz_type: str) -> int:
    last = QuizAttempt.objects.filter(
        user=user, topic=topic, quiz_type=quiz_type,
    ).order_by("-attempt_number").values_list("attempt_number", flat=True).first()
    return (last or 0) + 1


def global_leaderboard(limit=50):
    User = get_user_model()

    all_users = User.objects.select_related("preferences").annotate(
        total_points=Coalesce(
            Sum("subject_progress__points"), Value(0)
        ),
    ).order_by("-total_points", "id")

    result = []
    for rank, user in enumerate(all_users, 1):
        if user.preferences.leaderboard_visible:
            result.append({
                "rank": rank,
                "user_id": user.id,
                "display_name": user.display_name or user.email,
                "total_points": user.total_points,
            })
            if len(result) >= limit:
                break

    return result


def topic_leaderboard(topic_id, limit=20):
    progress_qs = TopicProgress.objects.filter(
        topic_id=topic_id, points__gt=0,
    ).select_related("user__preferences").order_by("-points", "user_id")

    result = []
    for rank, tp in enumerate(progress_qs, 1):
        if tp.user.preferences.leaderboard_visible:
            result.append({
                "rank": rank,
                "user_id": tp.user_id,
                "display_name": tp.user.display_name or tp.user.email,
                "points": tp.points,
            })
            if len(result) >= limit:
                break

    return result


def others_learning(topic_id, viewing_user, limit=10):
    topic = Topic.objects.get(id=topic_id)
    viewing_usp = UserSubjectProgress.objects.filter(
        user=viewing_user, subject=topic.subject,
    ).first()
    viewing_level = viewing_usp.level_unlocked if viewing_usp else 1

    other_progress = TopicProgress.objects.filter(
        topic=topic,
    ).exclude(
        user=viewing_user,
    ).select_related("user").filter(
        user__preferences__others_learning_visible=True,
    )

    entries = []
    for tp in other_progress:
        usp = UserSubjectProgress.objects.filter(
            user=tp.user, subject=topic.subject,
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

        entries.append({
            "priority": priority,
            "user_id": tp.user_id,
            "display_name": tp.user.display_name or tp.user.email,
            "status": usp.status,
            "level": tp.topic.level,
        })

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


def mark_resource_links_viewed(user, topic) -> TopicProgress:
    tp, _ = TopicProgress.objects.get_or_create(user=user, topic=topic)
    tp.resource_links_viewed_at = timezone.now()
    tp.save(update_fields=["resource_links_viewed_at"])
    return tp


def can_take_advanced_quiz(user, topic) -> bool:
    tp = TopicProgress.objects.filter(user=user, topic=topic).first()
    if tp is None:
        return False
    if tp.status not in (TopicProgress.Status.PASSED, TopicProgress.Status.ADVANCED_PASSED):
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
            raise ValueError("Advanced quiz not available: pass the normal quiz and engage with resource links first")
    else:
        if tp.status == TopicProgress.Status.PASSED:
            raise ValueError("Normal quiz already passed for this topic")

    last_attempt_time = QuizAttempt.objects.filter(
        user=user, topic=topic, quiz_type=quiz_type,
    ).order_by("-created_at").values_list("created_at", flat=True).first()
    if last_attempt_time is not None:
        cooldown_end = last_attempt_time + timedelta(hours=RETRY_COOLDOWN_HOURS)
        if timezone.now() < cooldown_end:
            remaining = (cooldown_end - timezone.now()).seconds // 60
            raise ValueError(f"Please wait {remaining} minutes before retaking this quiz")

    schema = NORMAL_QUIZ_SCHEMA if quiz_type == QuizAttempt.QuizType.NORMAL else ADVANCED_QUIZ_SCHEMA
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
        raise ValueError(
            f"Expected {len(questions)} answers, got {len(answers)}"
        )

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
            if tp.status not in (TopicProgress.Status.PASSED, TopicProgress.Status.ADVANCED_PASSED):
                tp.status = TopicProgress.Status.PASSED
                tp.completed_at = timezone.now()
                tp.save(update_fields=["status", "completed_at"])
        else:
            tp.status = TopicProgress.Status.ADVANCED_PASSED
            tp.save(update_fields=["status"])

    return attempt
