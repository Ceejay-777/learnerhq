from django.conf import settings
from django.db import models
from pgvector.django import HalfVectorField


class Subject(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ARCHIVED = "ARCHIVED", "Archived"

    name = models.CharField(max_length=255, unique=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    embedding = HalfVectorField(dimensions=768, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Topic(models.Model):
    class Level(models.IntegerChoices):
        ONE = 1, "Level 1"
        TWO = 2, "Level 2"
        THREE = 3, "Level 3"

    class ContentStatus(models.TextChoices):
        NOT_GENERATED = "NOT_GENERATED", "Not Generated"
        GENERATING = "GENERATING", "Generating"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Failed"

    class ReviewStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PASSED = "PASSED", "Passed"
        FAILED = "FAILED", "Failed"
        FLAGGED = "FLAGGED", "Flagged"

    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="topics"
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    resource_links = models.JSONField(blank=True, default=list)
    level = models.PositiveSmallIntegerField(choices=Level.choices)
    order = models.PositiveIntegerField()
    content_status = models.CharField(
        max_length=16, choices=ContentStatus.choices, default=ContentStatus.NOT_GENERATED
    )
    review_status = models.CharField(
        max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
    )
    review_attempts = models.PositiveSmallIntegerField(default=0)
    generation_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("subject", "order")]

    def __str__(self):
        return f"{self.title} ({self.subject.name})"


class UserSubjectProgress(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        COMPLETED = "COMPLETED", "Completed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subject_progress"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="user_progress"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    points = models.IntegerField(default=0)
    level_unlocked = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    needs_subject_selection = models.BooleanField(default=False)
    selection_pending_since = models.DateTimeField(null=True, blank=True)

    notification_frequency_hours = models.PositiveSmallIntegerField(default=24)
    next_due_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("user", "subject")]

    def __str__(self):
        return f"{self.user.email} - {self.subject.name} ({self.status})"


class UserInterest(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="interests"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="interested_users"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "subject")]

    def __str__(self):
        return f"{self.user.email} interested in {self.subject.name}"


class TopicProgress(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "NOT_STARTED", "Not Started"
        READING = "READING", "Reading"
        QUIZ_READY = "QUIZ_READY", "Quiz Ready"
        PASSED = "PASSED", "Passed"
        ADVANCED_PASSED = "ADVANCED_PASSED", "Advanced Passed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topic_progress"
    )
    topic = models.ForeignKey(
        Topic, on_delete=models.CASCADE, related_name="user_progress"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.NOT_STARTED
    )
    normal_quiz_attempts = models.PositiveSmallIntegerField(default=0)
    advanced_quiz_attempts = models.PositiveSmallIntegerField(default=0)
    points = models.IntegerField(default=0)
    is_bonus = models.BooleanField(default=False)
    resource_links_viewed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "topic")]

    def __str__(self):
        return f"{self.user.email} - {self.topic.title} ({self.status})"


class QuizAttempt(models.Model):
    class QuizType(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        ADVANCED = "ADVANCED", "Advanced"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiz_attempts"
    )
    topic = models.ForeignKey(
        Topic, on_delete=models.CASCADE, related_name="quiz_attempts"
    )
    quiz_type = models.CharField(max_length=8, choices=QuizType.choices)
    attempt_number = models.PositiveSmallIntegerField()
    questions = models.JSONField()
    answers = models.JSONField(null=True, blank=True)
    score = models.PositiveIntegerField(default=0)
    total_points = models.PositiveIntegerField(default=0)
    passed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "topic", "quiz_type", "attempt_number")]

    def __str__(self):
        return f"{self.user.email} - {self.topic.title} ({self.quiz_type} #{self.attempt_number})"
