import pytest
from django.db import IntegrityError
from django.contrib.auth import get_user_model

from apps.learning.models import Subject, Topic, UserSubjectProgress, TopicProgress, QuizAttempt

User = get_user_model()


@pytest.mark.django_db
class TestSubjectModel:

    def test_creates_subject(self):
        subject = Subject.objects.create(name="Mathematics", status=Subject.Status.ACTIVE)
        assert subject.name == "Mathematics"
        assert subject.status == Subject.Status.ACTIVE
        assert subject.created_at is not None

    def test_subject_name_unique(self):
        Subject.objects.create(name="Mathematics")
        with pytest.raises(IntegrityError):
            Subject.objects.create(name="Mathematics")

    def test_subject_str(self):
        subject = Subject(name="Physics")
        assert str(subject) == "Physics"


@pytest.mark.django_db
class TestTopicModel:

    def test_creates_topic(self):
        subject = Subject.objects.create(name="Science")
        topic = Topic.objects.create(
            subject=subject,
            title="Newton's Laws",
            level=Topic.Level.ONE,
            order=1,
        )
        assert topic.title == "Newton's Laws"
        assert topic.subject == subject
        assert topic.level == Topic.Level.ONE
        assert topic.summary == ""
        assert topic.resource_links == []
        assert topic.content_status == Topic.ContentStatus.NOT_GENERATED
        assert topic.review_status == Topic.ReviewStatus.PENDING
        assert topic.review_attempts == 0
        assert topic.created_at is not None

    def test_topic_subject_order_unique(self):
        subject = Subject.objects.create(name="Science")
        Topic.objects.create(subject=subject, title="Topic 1", level=Topic.Level.ONE, order=1)
        with pytest.raises(IntegrityError):
            Topic.objects.create(subject=subject, title="Topic 2", level=Topic.Level.ONE, order=1)

    def test_topic_level_choices(self):
        subject = Subject.objects.create(name="Science")
        topic = Topic.objects.create(
            subject=subject, title="Test", level=2, order=1
        )
        assert topic.level == Topic.Level.TWO

    def test_topic_str(self):
        subject = Subject(name="Science")
        topic = Topic(subject=subject, title="Thermodynamics")
        assert str(topic) == "Thermodynamics (Science)"


@pytest.mark.django_db
class TestUserSubjectProgress:

    def test_creates_progress(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Math")
        progress = UserSubjectProgress.objects.create(user=user, subject=subject)
        assert progress.user == user
        assert progress.subject == subject
        assert progress.status == UserSubjectProgress.Status.ACTIVE
        assert progress.points == 0
        assert progress.level_unlocked == 1
        assert progress.created_at is not None

    def test_user_subject_unique(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Math")
        UserSubjectProgress.objects.create(user=user, subject=subject)
        with pytest.raises(IntegrityError):
            UserSubjectProgress.objects.create(user=user, subject=subject)

    def test_str(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Math")
        progress = UserSubjectProgress.objects.create(user=user, subject=subject)
        assert str(progress) == "a@b.com - Math (ACTIVE)"


@pytest.mark.django_db
class TestTopicProgress:

    def test_creates_topic_progress(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Science")
        topic = Topic.objects.create(subject=subject, title="Physics", level=Topic.Level.ONE, order=1)
        tp = TopicProgress.objects.create(user=user, topic=topic)
        assert tp.user == user
        assert tp.topic == topic
        assert tp.status == TopicProgress.Status.NOT_STARTED
        assert tp.normal_quiz_attempts == 0
        assert tp.advanced_quiz_attempts == 0
        assert tp.points == 0
        assert tp.completed_at is None
        assert tp.created_at is not None

    def test_user_topic_unique(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Science")
        topic = Topic.objects.create(subject=subject, title="Physics", level=Topic.Level.ONE, order=1)
        TopicProgress.objects.create(user=user, topic=topic)
        with pytest.raises(IntegrityError):
            TopicProgress.objects.create(user=user, topic=topic)

    def test_str(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Science")
        topic = Topic.objects.create(subject=subject, title="Physics", level=Topic.Level.ONE, order=1)
        tp = TopicProgress.objects.create(user=user, topic=topic)
        assert str(tp) == "a@b.com - Physics (NOT_STARTED)"


@pytest.mark.django_db
class TestQuizAttempt:

    def test_creates_attempt(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Science")
        topic = Topic.objects.create(subject=subject, title="Physics", level=Topic.Level.ONE, order=1)
        attempt = QuizAttempt.objects.create(
            user=user,
            topic=topic,
            quiz_type=QuizAttempt.QuizType.NORMAL,
            attempt_number=1,
            questions=[{"q": "what?"}],
        )
        assert attempt.user == user
        assert attempt.topic == topic
        assert attempt.quiz_type == QuizAttempt.QuizType.NORMAL
        assert attempt.attempt_number == 1
        assert attempt.questions == [{"q": "what?"}]
        assert attempt.answers is None
        assert attempt.score == 0
        assert attempt.total_points == 0
        assert attempt.passed is False
        assert attempt.created_at is not None

    def test_attempt_number_scoped_per_user_topic_type(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Science")
        topic = Topic.objects.create(subject=subject, title="Physics", level=Topic.Level.ONE, order=1)
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.NORMAL,
            attempt_number=1, questions=[],
        )
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.ADVANCED,
            attempt_number=1, questions=[],
        )
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.NORMAL,
            attempt_number=2, questions=[],
        )
        with pytest.raises(IntegrityError):
            QuizAttempt.objects.create(
                user=user, topic=topic, quiz_type=QuizAttempt.QuizType.NORMAL,
                attempt_number=1, questions=[],
            )

    def test_str(self):
        user = User.objects.create_user(email="a@b.com", password="p")
        subject = Subject.objects.create(name="Science")
        topic = Topic.objects.create(subject=subject, title="Physics", level=Topic.Level.ONE, order=1)
        attempt = QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.NORMAL,
            attempt_number=1, questions=[],
        )
        assert str(attempt) == "a@b.com - Physics (NORMAL #1)"
