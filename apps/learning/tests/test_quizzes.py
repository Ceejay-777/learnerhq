import pytest
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.learning.models import (
    Subject, Topic, TopicProgress, QuizAttempt, UserSubjectProgress,
)
from apps.learning.services import (
    generate_quiz, submit_quiz, mark_resource_links_viewed,
    can_take_advanced_quiz, _next_attempt_number, _validate_quiz_response,
)
from apps.learning.exceptions import GenerationError

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p")


@pytest.fixture
def subject():
    return Subject.objects.create(name="World War II")


@pytest.fixture
def topic(subject):
    return Topic.objects.create(
        subject=subject, title="Causes of WW2", level=1, order=1,
        summary="The Treaty of Versailles, rise of fascism, and failure of appeasement led to WW2.",
        resource_links=[
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Causes_of_WW2", "type": "wikipedia"},
            {"title": "Article", "url": "https://example.com/article", "type": "article"},
        ],
    )


def _make_quiz_data(questions_count=5, total_points=100):
    return {
        "questions": [
            {
                "question": f"Test question {i}?",
                "options": [f"Option A for {i}", f"Option B for {i}", f"Option C for {i}", f"Option D for {i}"],
                "correct_index": 0,
                "explanation": f"This is why answer A is correct for question {i}.",
            }
            for i in range(questions_count)
        ],
        "total_points": total_points,
    }


@pytest.mark.django_db
class TestGenerateQuiz:

    @patch("apps.learning.services.generate_content")
    def test_normal_quiz_creates_attempt(self, mock_gen, user, topic):
        mock_gen.return_value = _make_quiz_data(5, 100)
        attempt = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert attempt.quiz_type == QuizAttempt.QuizType.NORMAL
        assert attempt.attempt_number == 1
        assert len(attempt.questions) == 5
        assert attempt.total_points == 100
        assert attempt.user == user
        assert attempt.topic == topic

    @patch("apps.learning.services.generate_content")
    def test_advanced_quiz_creates_attempt(self, mock_gen, user, topic):
        mock_gen.return_value = _make_quiz_data(10, 200)
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        attempt = generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)
        assert attempt.quiz_type == QuizAttempt.QuizType.ADVANCED
        assert len(attempt.questions) == 10
        assert attempt.total_points == 200

    @patch("apps.learning.services.generate_content")
    def test_advanced_quiz_blocked_without_pass(self, mock_gen, user, topic):
        with pytest.raises(ValueError, match="Advanced quiz not available"):
            generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)

    @patch("apps.learning.services.generate_content")
    def test_advanced_quiz_blocked_without_resource_links(self, mock_gen, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
        )
        with pytest.raises(ValueError, match="Advanced quiz not available"):
            generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)

    @patch("apps.learning.services.generate_content")
    def test_advanced_quiz_blocked_after_advanced_passed(self, mock_gen, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.ADVANCED_PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        with pytest.raises(ValueError, match="Advanced quiz not available"):
            generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)

    @patch("apps.learning.services.generate_content")
    def test_normal_quiz_blocked_after_pass(self, mock_gen, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
        )
        with pytest.raises(ValueError, match="Normal quiz already passed"):
            generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)

    @patch("apps.learning.services.generate_content")
    def test_attempt_number_increments(self, mock_gen, user, topic):
        mock_gen.return_value = _make_quiz_data(5, 100)
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert a1.attempt_number == 1
        QuizAttempt.objects.filter(id=a1.id).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        a2 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert a2.attempt_number == 2

    @patch("apps.learning.services.generate_content")
    def test_attempt_number_independent_per_type(self, mock_gen, user, topic):
        mock_gen.return_value = _make_quiz_data(5, 100)
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert a1.attempt_number == 1
        QuizAttempt.objects.filter(id=a1.id).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        tp = TopicProgress.objects.get(user=user, topic=topic)
        tp.status = TopicProgress.Status.PASSED
        tp.resource_links_viewed_at = timezone.now()
        tp.save(update_fields=["status", "resource_links_viewed_at"])
        mock_gen.return_value = _make_quiz_data(10, 200)
        a2 = generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)
        assert a2.attempt_number == 1

    @patch("apps.learning.services.generate_content")
    def test_increments_normal_quiz_attempts_on_submit(self, mock_gen, user, topic):
        mock_gen.return_value = _make_quiz_data(5, 100)
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.normal_quiz_attempts == 0
        submit_quiz(a1.id, [0, 0, 0, 0, 0])
        tp.refresh_from_db()
        assert tp.normal_quiz_attempts == 1

    @patch("apps.learning.services.generate_content")
    def test_increments_advanced_quiz_attempts_on_submit(self, mock_gen, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        mock_gen.return_value = _make_quiz_data(10, 200)
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.advanced_quiz_attempts == 0
        submit_quiz(a1.id, [0] * 10)
        tp.refresh_from_db()
        assert tp.advanced_quiz_attempts == 1

    @patch("apps.learning.services.generate_content")
    def test_retake_cooldown(self, mock_gen, user, topic):
        mock_gen.return_value = _make_quiz_data(5, 100)
        generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        with pytest.raises(ValueError, match="Please wait"):
            generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)

    @patch("apps.learning.services.generate_content")
    def test_retake_cooldown_expires(self, mock_gen, user, topic):
        mock_gen.return_value = _make_quiz_data(5, 100)
        generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        QuizAttempt.objects.filter(user=user, topic=topic).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        a2 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert a2.attempt_number == 2

    @patch("apps.learning.services.generate_content")
    def test_prior_missed_included_in_prompt(self, mock_gen, user, topic):
        mock_gen.return_value = _make_quiz_data(5, 100)
        with patch("apps.learning.services.generate_content") as gen:
            gen.return_value = _make_quiz_data(5, 100)
            generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL,
                          prior_missed_questions=["What caused WW2?"])
            call_kwargs = gen.call_args[1]
            assert "What caused WW2?" in call_kwargs["prompt"]

    @patch("apps.learning.services.generate_content")
    def test_malformed_retry_then_succeeds(self, mock_gen, user, topic):
        mock_gen.side_effect = [
            {"questions": [], "total_points": 50},
            _make_quiz_data(5, 100),
        ]
        attempt = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert len(attempt.questions) == 5
        assert mock_gen.call_count == 2

    @patch("apps.learning.services.generate_content")
    def test_malformed_exhausted_raises(self, mock_gen, user, topic):
        mock_gen.return_value = {"questions": [], "total_points": 50}
        with pytest.raises(GenerationError):
            generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert mock_gen.call_count == 2


@pytest.mark.django_db
class TestSubmitQuiz:

    VALID_ANSWERS = [0, 0, 0, 0, 0]

    def _create_attempt(self, user, topic, quiz_type="NORMAL", total_points=100):
        return QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=quiz_type,
            attempt_number=1,
            questions=_make_quiz_data(5, total_points)["questions"],
            total_points=total_points,
        )

    def test_passed_updates_score_and_status(self, user, topic):
        attempt = self._create_attempt(user, topic)
        result = submit_quiz(attempt.id, self.VALID_ANSWERS)
        assert result.passed is True
        assert result.score > 0
        assert result.answers == self.VALID_ANSWERS

    def test_failed_sets_passed_false(self, user, topic):
        attempt = self._create_attempt(user, topic)
        wrong = [1, 2, 3, 0, 1]
        result = submit_quiz(attempt.id, wrong)
        assert result.passed is False
        assert result.score == 0

    def test_passed_earns_points_on_topic_progress(self, user, topic):
        attempt = self._create_attempt(user, topic)
        result = submit_quiz(attempt.id, self.VALID_ANSWERS)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.points == result.score

    def test_passed_earns_points_on_subject_progress(self, user, topic, subject):
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        attempt = self._create_attempt(user, topic)
        result = submit_quiz(attempt.id, self.VALID_ANSWERS)
        usp.refresh_from_db()
        assert usp.points == result.score

    def test_failed_earns_no_points(self, user, topic):
        attempt = self._create_attempt(user, topic)
        wrong = [1, 2, 3, 0, 1]
        submit_quiz(attempt.id, wrong)
        tp = TopicProgress.objects.filter(user=user, topic=topic).first()
        assert tp is None or tp.points == 0

    def test_passed_normal_updates_status_to_passed(self, user, topic):
        attempt = self._create_attempt(user, topic)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.status == TopicProgress.Status.PASSED
        assert tp.completed_at is not None

    def test_passed_advanced_updates_status(self, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
        )
        attempt = self._create_attempt(user, topic, "ADVANCED", 200)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.status == TopicProgress.Status.ADVANCED_PASSED

    def test_wrong_answer_count_raises(self, user, topic):
        attempt = self._create_attempt(user, topic)
        with pytest.raises(ValueError, match="Expected 5 answers"):
            submit_quiz(attempt.id, [0, 1, 2])

    def test_duplicate_submission_raises(self, user, topic):
        attempt = self._create_attempt(user, topic)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        with pytest.raises(ValueError, match="already submitted"):
            submit_quiz(attempt.id, self.VALID_ANSWERS)

    def test_normal_quiz_passed_does_not_allow_retake(self, user, topic):
        attempt = self._create_attempt(user, topic)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        with pytest.raises(ValueError, match="Normal quiz already passed"):
            with patch("apps.learning.services.generate_content") as mock_gen:
                mock_gen.return_value = _make_quiz_data(5, 100)
                generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)

    def test_advanced_quiz_passed_does_not_allow_retake(self, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        attempt = self._create_attempt(user, topic, "ADVANCED", 200)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        assert not can_take_advanced_quiz(user, topic)


@pytest.mark.django_db
class TestValidateQuizResponse:

    def test_valid_normal_passes(self):
        data = _make_quiz_data(5, 100)
        result = _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)
        assert result == data

    def test_valid_advanced_passes(self):
        data = _make_quiz_data(10, 200)
        result = _validate_quiz_response(data, QuizAttempt.QuizType.ADVANCED)
        assert result == data

    def test_wrong_question_count_normal(self):
        data = _make_quiz_data(2, 100)
        with pytest.raises(GenerationError, match="4-6"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_wrong_question_count_advanced(self):
        data = _make_quiz_data(5, 200)
        with pytest.raises(GenerationError, match="9-12"):
            _validate_quiz_response(data, QuizAttempt.QuizType.ADVANCED)

    def test_wrong_points_normal(self):
        data = _make_quiz_data(5, 50)
        with pytest.raises(GenerationError, match="90-110"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_wrong_points_advanced(self):
        data = _make_quiz_data(10, 100)
        with pytest.raises(GenerationError, match="180-220"):
            _validate_quiz_response(data, QuizAttempt.QuizType.ADVANCED)

    def test_missing_question_text(self):
        data = _make_quiz_data(5, 100)
        data["questions"][0]["question"] = ""
        with pytest.raises(GenerationError, match="missing text"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_invalid_correct_index(self):
        data = _make_quiz_data(5, 100)
        data["questions"][0]["correct_index"] = 5
        with pytest.raises(GenerationError, match="correct_index"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_missing_explanation(self):
        data = _make_quiz_data(5, 100)
        data["questions"][0]["explanation"] = ""
        with pytest.raises(GenerationError, match="missing explanation"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)


@pytest.mark.django_db
class TestCanTakeAdvancedQuiz:

    def test_no_topic_progress_returns_false(self, user, topic):
        assert can_take_advanced_quiz(user, topic) is False

    def test_not_passed_normal_returns_false(self, user, topic):
        TopicProgress.objects.create(user=user, topic=topic, status=TopicProgress.Status.READING)
        assert can_take_advanced_quiz(user, topic) is False

    def test_passed_but_no_resource_links_returns_false(self, user, topic):
        TopicProgress.objects.create(user=user, topic=topic, status=TopicProgress.Status.PASSED)
        assert can_take_advanced_quiz(user, topic) is False

    def test_passed_and_viewed_links_returns_true(self, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        assert can_take_advanced_quiz(user, topic) is True

    def test_advanced_already_passed_returns_false(self, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.ADVANCED_PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        assert can_take_advanced_quiz(user, topic) is False


@pytest.mark.django_db
class TestMarkResourceLinksViewed:

    def test_creates_topic_progress_if_missing(self, user, topic):
        tp = mark_resource_links_viewed(user, topic)
        assert tp.resource_links_viewed_at is not None
        assert tp.user == user
        assert tp.topic == topic

    def test_updates_existing(self, user, topic):
        tp = TopicProgress.objects.create(user=user, topic=topic)
        assert tp.resource_links_viewed_at is None
        tp2 = mark_resource_links_viewed(user, topic)
        assert tp2.resource_links_viewed_at is not None


@pytest.mark.django_db
class TestNextAttemptNumber:

    def test_first_attempt(self, user, topic):
        assert _next_attempt_number(user, topic, QuizAttempt.QuizType.NORMAL) == 1

    def test_second_attempt(self, user, topic):
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.NORMAL,
            attempt_number=1, questions=[],
        )
        assert _next_attempt_number(user, topic, QuizAttempt.QuizType.NORMAL) == 2

    def test_independent_per_type(self, user, topic):
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.NORMAL,
            attempt_number=1, questions=[],
        )
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.ADVANCED,
            attempt_number=1, questions=[],
        )
        assert _next_attempt_number(user, topic, QuizAttempt.QuizType.NORMAL) == 2
        assert _next_attempt_number(user, topic, QuizAttempt.QuizType.ADVANCED) == 2
