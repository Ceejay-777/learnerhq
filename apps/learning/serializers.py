from rest_framework import serializers

from .models import Subject


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "name", "created_at"]
        extra_kwargs = {
            'name': {'help_text': 'Name of the subject.'},
        }


class SubjectSuggestionSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Subject ID.")
    name = serializers.CharField(help_text="Subject name.")
    reason = serializers.CharField(help_text="Why this subject was suggested.")


class LevelCheckResponseSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=["none", "level_up", "subject_completed"],
        help_text="Result of the level progression check.",
    )
    new_level_unlocked = serializers.IntegerField(
        required=False, help_text="New level unlocked (only if action is level_up).",
    )
    slots_available = serializers.IntegerField(
        required=False, help_text="Available subject slots (only if action is level_up).",
    )
    suggestions = SubjectSuggestionSerializer(
        many=True, required=False, help_text="Suggested next subjects.",
    )
    needs_subject_selection = serializers.BooleanField(
        required=False, help_text="Whether user needs to pick a new subject (only if subject_completed).",
    )


class ExploreSubjectSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Subject ID.")
    name = serializers.CharField(help_text="Subject name.")
    enrollment_count = serializers.IntegerField(help_text="Number of active learners.")
    is_enrolled = serializers.BooleanField(help_text="Whether the current user is enrolled.")
    is_completed = serializers.BooleanField(help_text="Whether the current user has completed this subject.")
    is_interested = serializers.BooleanField(help_text="Whether the current user has marked interest.")


class NotificationFrequencySerializer(serializers.Serializer):
    frequency_hours = serializers.IntegerField(
        min_value=1, max_value=24, help_text="Notification interval in hours (1-24).",
    )


class NotificationStatusSerializer(serializers.Serializer):
    frequency_hours = serializers.IntegerField(
        allow_null=True, help_text="Current notification interval in hours, or null if unset.",
    )
    next_due_at = serializers.DateTimeField(
        allow_null=True, help_text="When the next notification is due, or null if unset.",
    )


class LeaderboardEntrySerializer(serializers.Serializer):
    rank = serializers.IntegerField(help_text="Position on the leaderboard (1-based).")
    user_id = serializers.IntegerField(help_text="User ID.")
    display_name = serializers.CharField(help_text="User display name or email fallback.")
    total_points = serializers.IntegerField(help_text="Total accumulated points across all subjects.")


class TopicLeaderboardEntrySerializer(serializers.Serializer):
    rank = serializers.IntegerField(help_text="Position on the topic leaderboard (1-based).")
    user_id = serializers.IntegerField(help_text="User ID.")
    display_name = serializers.CharField(help_text="User display name or email fallback.")
    points = serializers.IntegerField(help_text="Points earned on this topic.")


class OthersLearningEntrySerializer(serializers.Serializer):
    user_id = serializers.IntegerField(help_text="User ID.")
    display_name = serializers.CharField(help_text="User display name or email fallback.")
    status = serializers.CharField(help_text="User's progress status for this subject.")
    level = serializers.IntegerField(help_text="Current level the user is on.")


class GenerateQuizRequestSerializer(serializers.Serializer):
    quiz_type = serializers.ChoiceField(
        choices=["NORMAL", "ADVANCED"],
        required=False,
        default="NORMAL",
        help_text="Type of quiz to generate (NORMAL or ADVANCED).",
    )
    prior_missed_questions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="List of question texts the learner previously got wrong, for focused retry.",
    )


class GenerateQuizResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Quiz attempt ID.")
    quiz_type = serializers.CharField(help_text="Type of quiz generated.")
    attempt_number = serializers.IntegerField(help_text="Attempt number for this quiz type and topic.")
    questions = serializers.JSONField(help_text="List of quiz questions with options and explanations.")
    total_points = serializers.IntegerField(help_text="Maximum possible points.")


class SubmitQuizRequestSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField(help_text="ID of the quiz attempt to submit.")
    answers = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=3),
        help_text="List of selected option indices (0-3), one per question.",
    )


class SubmitQuizResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Quiz attempt ID.")
    passed = serializers.BooleanField(help_text="Whether the user passed (score >= 60% of total_points).")
    score = serializers.IntegerField(help_text="Points earned.")
    total_points = serializers.IntegerField(help_text="Maximum possible points.")
    attempt_number = serializers.IntegerField(help_text="Attempt number.")
    quiz_type = serializers.CharField(help_text="Type of quiz.")


class CreateSubjectSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, help_text="Subject name to create or resolve.")


class TopicDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Topic ID.")
    title = serializers.CharField(help_text="Topic title.")
    summary = serializers.CharField(help_text="Generated content summary for this topic.")
    resource_links = serializers.JSONField(help_text="List of external resource links.")
    level = serializers.IntegerField(help_text="Difficulty level (1-3).")
    order = serializers.IntegerField(help_text="Order within the subject's roadmap.")
    content_status = serializers.CharField(help_text="Content generation status.")
    review_status = serializers.CharField(help_text="Content review status.")
    subject_id = serializers.IntegerField(help_text="Parent subject ID.")
    subject_name = serializers.CharField(help_text="Parent subject name.")


class EnrolledSubjectListSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Subject ID.")
    name = serializers.CharField(help_text="Subject name.")
    status = serializers.CharField(help_text="Enrollment status (ACTIVE or COMPLETED).")
    points = serializers.IntegerField(help_text="Total points earned in this subject.")
    level_unlocked = serializers.IntegerField(help_text="Highest accessible level (1-3).")
    topics_total = serializers.IntegerField(help_text="Total topics in the subject roadmap.")
    topics_passed = serializers.IntegerField(help_text="Topics passed by the user (normal or advanced).")
    topics_advanced_passed = serializers.IntegerField(help_text="Topics where the advanced quiz was passed.")
    percent_complete = serializers.IntegerField(help_text="Percentage of topics passed (0-100).")
    created_at = serializers.DateTimeField(help_text="When the user enrolled.")
    notification_frequency_hours = serializers.IntegerField(help_text="Notification interval in hours (1-24).")
    next_due_at = serializers.DateTimeField(allow_null=True, help_text="Next scheduled notification, or null.")


class SubjectTopicProgressSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Topic ID.")
    title = serializers.CharField(help_text="Topic title.")
    level = serializers.IntegerField(help_text="Difficulty level (1-3).")
    order = serializers.IntegerField(help_text="Order within the roadmap.")
    content_status = serializers.CharField(help_text="Content generation status.")
    review_status = serializers.CharField(help_text="Content review status.")
    user_progress_status = serializers.CharField(allow_null=True, help_text="User's progress on this topic, or null if not started.")
    is_passed = serializers.BooleanField(help_text="Whether the user has passed this topic.")
    completed_at = serializers.DateTimeField(allow_null=True, help_text="When the user completed this topic, or null.")


class SubjectDetailLevelSerializer(serializers.Serializer):
    level = serializers.IntegerField(help_text="Level number (1-3).")
    total = serializers.IntegerField(help_text="Number of topics in this level.")
    passed = serializers.IntegerField(help_text="Number of topics the user passed in this level.")
    threshold = serializers.IntegerField(help_text="Number of topics needed to advance (80% of total).")
    is_unlocked = serializers.BooleanField(help_text="Whether this level is accessible to the user.")


class SubjectDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Subject ID.")
    name = serializers.CharField(help_text="Subject name.")
    status = serializers.CharField(help_text="Enrollment status.")
    points = serializers.IntegerField(help_text="Total points earned.")
    level_unlocked = serializers.IntegerField(help_text="Highest accessible level.")
    created_at = serializers.DateTimeField(help_text="When the user enrolled.")
    notification_frequency_hours = serializers.IntegerField(help_text="Notification interval in hours.")
    next_due_at = serializers.DateTimeField(allow_null=True, help_text="Next scheduled notification, or null.")
    levels = SubjectDetailLevelSerializer(many=True, help_text="Per-level progress rollups.")
    topics = SubjectTopicProgressSerializer(many=True, help_text="Full topic roadmap with per-user progress.")


class ResourceLinksViewedResponseSerializer(serializers.Serializer):
    status = serializers.CharField(help_text="Current topic progress status.")
    resource_links_viewed_at = serializers.DateTimeField(
        allow_null=True, help_text="When resource links were viewed, or null.",
    )
