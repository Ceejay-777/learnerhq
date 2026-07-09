import pytest
from django.contrib.auth import get_user_model
from apps.learning.models import Subject, Topic

User = get_user_model()


@pytest.mark.django_db
class TestTopicModelExtended:

    def test_generation_started_at_defaults_to_none(self):
        subject = Subject.objects.create(name="Test")
        topic = Topic.objects.create(
            subject=subject, title="T1", level=Topic.Level.ONE, order=1,
        )
        assert topic.generation_started_at is None

    def test_generation_started_at_settable(self):
        from django.utils import timezone
        subject = Subject.objects.create(name="Test")
        now = timezone.now()
        topic = Topic.objects.create(
            subject=subject, title="T1", level=Topic.Level.ONE, order=1,
            generation_started_at=now,
        )
        topic.refresh_from_db()
        assert topic.generation_started_at is not None


from apps.learning.services import _validate_topic_content
from apps.learning.exceptions import GenerationError


VALID_DATA = {
    "summary": "A short summary of the topic. It should be a few sentences explaining key concepts.",
    "resource_links": [
        {"title": "Wikipedia Article", "url": "https://en.wikipedia.org/wiki/Example", "type": "article"},
        {"title": "Educational Video", "url": "https://youtube.com/watch?v=xyz", "type": "video"},
    ],
}


@pytest.mark.django_db
class TestTopicContentValidation:

    def test_valid_content_passes(self):
        result = _validate_topic_content(VALID_DATA)
        assert result["summary"] == VALID_DATA["summary"]
        assert result["resource_links"] == VALID_DATA["resource_links"]

    def test_missing_summary_raises_error(self):
        with pytest.raises(GenerationError, match="summary"):
            _validate_topic_content({"resource_links": []})

    def test_empty_summary_raises_error(self):
        with pytest.raises(GenerationError):
            _validate_topic_content({"summary": "", "resource_links": []})

    def test_missing_resource_links_raises_error(self):
        with pytest.raises(GenerationError):
            _validate_topic_content({"summary": "x"})

    def test_too_few_resource_links_raises_error(self):
        data = {"summary": "x", "resource_links": [{"title": "x", "url": "x", "type": "article"}]}
        with pytest.raises(GenerationError):
            _validate_topic_content(data)

    def test_invalid_link_type_raises_error(self):
        data = {"summary": "x", "resource_links": [
            {"title": "x", "url": "x", "type": "invalid"},
            {"title": "y", "url": "y", "type": "article"},
        ]}
        with pytest.raises(GenerationError):
            _validate_topic_content(data)

    def test_missing_link_title_raises_error(self):
        data = {"summary": "x", "resource_links": [
            {"url": "x", "type": "article"},
            {"title": "y", "url": "y", "type": "article"},
        ]}
        with pytest.raises(GenerationError):
            _validate_topic_content(data)


from unittest.mock import patch
from django.utils import timezone
from apps.learning.services import (
    ensure_topic_content_ready, generate_topic_content, review_topic_content,
)
from apps.ai.exceptions import ProviderError


@pytest.mark.django_db
class TestServicesContentGeneration:

    @pytest.fixture
    def subject(self):
        return Subject.objects.create(name="World War II")

    @pytest.fixture
    def topic(self, subject):
        return Topic.objects.create(
            subject=subject, title="Causes of WW2", level=Topic.Level.ONE, order=1,
        )

    # --- ensure_topic_content_ready ---

    def test_ensure_ready_claims_lock(self, topic):
        assert topic.content_status == Topic.ContentStatus.NOT_GENERATED
        claimed = ensure_topic_content_ready(topic.id)
        assert claimed is True
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.GENERATING
        assert topic.generation_started_at is not None

    def test_ensure_ready_returns_false_when_already_generating(self, topic):
        ensure_topic_content_ready(topic.id)
        claimed = ensure_topic_content_ready(topic.id)
        assert claimed is False

    def test_ensure_ready_returns_false_when_already_ready(self, topic):
        Topic.objects.filter(id=topic.id).update(content_status=Topic.ContentStatus.READY)
        claimed = ensure_topic_content_ready(topic.id)
        assert claimed is False

    def test_ensure_ready_returns_false_when_failed(self, topic):
        Topic.objects.filter(id=topic.id).update(content_status=Topic.ContentStatus.FAILED)
        claimed = ensure_topic_content_ready(topic.id)
        assert claimed is False

    # --- generate_topic_content ---

    @patch("apps.learning.services.generate_content")
    def test_generate_saves_summary_and_links(self, mock_gen, topic):
        mock_gen.return_value = {
            "summary": "A detailed summary about the causes of WW2.",
            "resource_links": [
                {"title": "Article", "url": "https://example.com/a", "type": "article"},
                {"title": "Video", "url": "https://example.com/v", "type": "video"},
            ],
        }
        generate_topic_content(topic.id)
        topic.refresh_from_db()
        assert "causes of ww2" in topic.summary.lower()
        assert len(topic.resource_links) == 2

    @patch("apps.learning.services.generate_content")
    def test_generate_malformed_retry_then_succeeds(self, mock_gen, topic):
        mock_gen.side_effect = [
            {"summary": "x", "resource_links": []},
            {"summary": "Valid summary.", "resource_links": [
                {"title": "A", "url": "https://a.com", "type": "article"},
                {"title": "B", "url": "https://b.com", "type": "video"},
            ]},
        ]
        generate_topic_content(topic.id)
        topic.refresh_from_db()
        assert topic.summary == "Valid summary."
        assert mock_gen.call_count == 2

    @patch("apps.learning.services.generate_content")
    def test_generate_malformed_exhausted_raises(self, mock_gen, topic):
        mock_gen.return_value = {"summary": "x", "resource_links": []}
        with pytest.raises(GenerationError):
            generate_topic_content(topic.id)
        assert mock_gen.call_count == 2

    @patch("apps.learning.services.generate_content")
    def test_generate_provider_error_propagates(self, mock_gen, topic):
        mock_gen.side_effect = ProviderError("API down", provider="gemini")
        with pytest.raises(ProviderError):
            generate_topic_content(topic.id)

    # --- review_topic_content ---

    @patch("apps.ai.services.review_content")
    def test_review_returns_true_when_passed(self, mock_review, topic):
        Topic.objects.filter(id=topic.id).update(summary="Some content.")
        mock_review.return_value = {"passed": True, "issues": []}
        result = review_topic_content(topic.id)
        assert result is True

    @patch("apps.ai.services.review_content")
    def test_review_returns_false_when_failed(self, mock_review, topic):
        Topic.objects.filter(id=topic.id).update(summary="Some content.")
        mock_review.return_value = {"passed": False, "issues": [{"severity": "error", "description": "Inaccurate"}]}
        result = review_topic_content(topic.id)
        assert result is False


from unittest.mock import PropertyMock
from apps.learning.tasks import generate_content_for_topic, review_content_for_topic


@pytest.mark.django_db
class TestGenTask:

    @pytest.fixture
    def subject(self):
        return Subject.objects.create(name="World War II")

    @pytest.fixture
    def topic(self, subject):
        return Topic.objects.create(
            subject=subject, title="Causes of WW2", level=Topic.Level.ONE, order=1,
        )

    @patch("apps.learning.tasks.review_content_for_topic.delay")
    @patch("apps.learning.services.generate_content")
    def test_gen_task_happy_path(self, mock_gen, mock_review_delay, topic):
        mock_gen.return_value = {
            "summary": "A summary.",
            "resource_links": [
                {"title": "A", "url": "https://a.com", "type": "article"},
                {"title": "B", "url": "https://b.com", "type": "video"},
            ],
        }
        generate_content_for_topic(topic.id)
        topic.refresh_from_db()
        assert topic.summary == "A summary."
        mock_review_delay.assert_called_once_with(topic.id)

    @patch("apps.learning.tasks.review_content_for_topic.delay")
    @patch("apps.learning.services.generate_content")
    def test_gen_task_skips_if_lock_held(self, mock_gen, mock_review_delay, topic):
        Topic.objects.filter(id=topic.id).update(content_status=Topic.ContentStatus.GENERATING)
        generate_content_for_topic(topic.id)
        mock_gen.assert_not_called()
        mock_review_delay.assert_not_called()

    @patch("apps.learning.tasks.review_content_for_topic.delay")
    @patch("apps.learning.services.generate_content")
    def test_gen_task_marks_failed_on_malformed_exhausted(self, mock_gen, mock_review_delay, topic):
        mock_gen.return_value = {"summary": "x", "resource_links": []}
        generate_content_for_topic(topic.id)
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.FAILED
        assert topic.review_status == Topic.ReviewStatus.FLAGGED
        mock_review_delay.assert_not_called()

    @patch("apps.learning.tasks.review_content_for_topic.delay")
    @patch("apps.learning.services.generate_content")
    def test_gen_task_provider_error_does_not_flag(self, mock_gen, mock_review_delay, topic):
        """ProviderError should propagate up for Celery's own retry."""
        mock_gen.side_effect = ProviderError("API down", provider="gemini")
        with pytest.raises(ProviderError):
            generate_content_for_topic(topic.id)
        mock_review_delay.assert_not_called()


@pytest.mark.django_db
class TestReviewTask:

    @pytest.fixture
    def subject(self):
        return Subject.objects.create(name="World War II")

    @pytest.fixture
    def topic(self, subject):
        return Topic.objects.create(
            subject=subject, title="Causes of WW2", level=Topic.Level.ONE, order=1,
            summary="Some content about WW2.",
        )

    @patch("apps.ai.services.review_content")
    def test_review_task_passes(self, mock_review, topic):
        mock_review.return_value = {"passed": True, "issues": []}
        review_content_for_topic(topic.id)
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.READY
        assert topic.review_status == Topic.ReviewStatus.PASSED

    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    @patch("apps.ai.services.review_content")
    def test_review_task_fails_then_retries(self, mock_review, mock_gen_delay, topic):
        mock_review.return_value = {"passed": False, "issues": [{"severity": "error", "description": "Bad"}]}
        review_content_for_topic(topic.id)
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.NOT_GENERATED
        assert topic.generation_started_at is None
        assert topic.review_attempts == 1
        mock_gen_delay.assert_called_once_with(topic.id)

    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    @patch("apps.ai.services.review_content")
    def test_review_task_fails_permanently(self, mock_review, mock_gen_delay, topic):
        Topic.objects.filter(id=topic.id).update(review_attempts=1)
        mock_review.return_value = {"passed": False, "issues": [{"severity": "error", "description": "Bad"}]}
        review_content_for_topic(topic.id)
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.FAILED
        assert topic.review_status == Topic.ReviewStatus.FLAGGED
        mock_gen_delay.assert_not_called()


from unittest.mock import ANY


@pytest.mark.django_db
class TestBufferAhead:

    @pytest.fixture
    def subject(self):
        return Subject.objects.create(name="World War II")

    @pytest.fixture
    def topics(self, subject):
        return [
            Topic.objects.create(subject=subject, title=f"T{i}", level=1, order=i)
            for i in range(1, 6)
        ]

    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    def test_ensure_buffer_ahead_triggers_next_two(self, mock_delay, subject, topics):
        from apps.learning.services import ensure_buffer_ahead
        ensure_buffer_ahead(topics[0])
        assert mock_delay.call_count == 2
        mock_delay.assert_any_call(topics[1].id)
        mock_delay.assert_any_call(topics[2].id)

    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    def test_skips_already_ready_topics(self, mock_delay, subject, topics):
        Topic.objects.filter(id=topics[1].id).update(content_status=Topic.ContentStatus.READY)
        from apps.learning.services import ensure_buffer_ahead
        ensure_buffer_ahead(topics[0])
        mock_delay.assert_called_once_with(topics[2].id)

    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    def test_skips_already_generating_topics(self, mock_delay, subject, topics):
        Topic.objects.filter(id=topics[1].id).update(content_status=Topic.ContentStatus.GENERATING)
        from apps.learning.services import ensure_buffer_ahead
        ensure_buffer_ahead(topics[0])
        mock_delay.assert_called_once_with(topics[2].id)

    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    def test_generate_initial_batch_triggers_first_three(self, mock_delay, subject):
        for i in range(1, 6):
            Topic.objects.create(subject=subject, title=f"T{i}", level=1, order=i)
        from apps.learning.services import generate_initial_batch
        generate_initial_batch(subject)
        assert mock_delay.call_count == 3

    def test_no_crash_when_no_next_topics(self, subject):
        topic = Topic.objects.create(subject=subject, title="Only one", level=1, order=1)
        from apps.learning.services import ensure_buffer_ahead
        ensure_buffer_ahead(topic)


import threading
from django.db import connections


@pytest.mark.django_db(transaction=True)
class TestConcurrentLock:

    def test_concurrent_triggers_only_one_wins(self):
        """Simulate near-simultaneous ensure_topic_content_ready calls
        using threading.Barrier to synchronize threads."""
        subject = Subject.objects.create(name="Physics")
        topic = Topic.objects.create(
            subject=subject, title="Quantum Mechanics", level=Topic.Level.ONE, order=1,
        )
        results = []
        errors = []

        def attempt():
            try:
                connections.close_all()

                from apps.learning.services import ensure_topic_content_ready
                result = ensure_topic_content_ready(topic.id)
                results.append(result)
            except Exception as e:
                errors.append(e)

        N = 5
        barrier = threading.Barrier(N)

        def synced_attempt():
            barrier.wait()
            attempt()

        threads = [threading.Thread(target=synced_attempt) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors occurred: {errors}"
        assert sum(results) == 1, f"Expected 1 winner, got {sum(results)}: {results}"
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.GENERATING
        assert topic.generation_started_at is not None


from django.utils import timezone as tz
from datetime import timedelta


@pytest.mark.django_db
class TestCleanupStuck:

    def test_resets_stuck_generations(self):
        from apps.learning.tasks import cleanup_stuck_generations
        subject = Subject.objects.create(name="Test")
        old = timezone.now() - timedelta(hours=2)
        topic = Topic.objects.create(
            subject=subject, title="Stuck", level=1, order=1,
            content_status=Topic.ContentStatus.GENERATING,
            generation_started_at=old,
        )
        cleanup_stuck_generations()
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.NOT_GENERATED
        assert topic.generation_started_at is None

    def test_leaves_recent_generations_alone(self):
        from apps.learning.tasks import cleanup_stuck_generations
        subject = Subject.objects.create(name="Test")
        recent = timezone.now() - timedelta(minutes=30)
        topic = Topic.objects.create(
            subject=subject, title="Recent", level=1, order=1,
            content_status=Topic.ContentStatus.GENERATING,
            generation_started_at=recent,
        )
        cleanup_stuck_generations()
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.GENERATING

    def test_ignores_not_stuck_statuses(self):
        from apps.learning.tasks import cleanup_stuck_generations
        subject = Subject.objects.create(name="Test")
        old = timezone.now() - timedelta(hours=2)
        r = Topic.objects.create(
            subject=subject, title="Ready", level=1, order=1,
            content_status=Topic.ContentStatus.READY,
            generation_started_at=old,
        )
        f = Topic.objects.create(
            subject=subject, title="Failed", level=1, order=2,
            content_status=Topic.ContentStatus.FAILED,
            generation_started_at=old,
        )
        cleanup_stuck_generations()
        assert Topic.objects.get(id=r.id).content_status == Topic.ContentStatus.READY
        assert Topic.objects.get(id=f.id).content_status == Topic.ContentStatus.FAILED


from unittest.mock import MagicMock, patch as mock_patch


@pytest.mark.django_db
class TestAIReviewContent:

    @mock_patch.dict("os.environ", {"GROQ_API_KEY": "test-key"})
    @mock_patch("groq.Groq")
    def test_review_calls_groq_and_returns_result(self, mock_groq_class):
        from apps.ai.services import review_content
        mock_client = mock_groq_class.return_value
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"passed": false, "issues": [{"severity": "error", "description": "Inaccurate"}]}'))]
        )
        result = review_content("Some content", criteria={"topic_title": "Test", "subject_name": "Test"})
        assert result["passed"] is False
        assert len(result["issues"]) == 1

    @mock_patch.dict("os.environ", {"GROQ_API_KEY": "test-key"})
    @mock_patch("groq.Groq")
    def test_review_falls_back_on_api_error(self, mock_groq_class):
        from apps.ai.exceptions import ProviderError
        from apps.ai.services import review_content
        mock_groq_class.side_effect = Exception("API down")
        with pytest.raises(ProviderError, match="API down"):
            review_content("Some content")
