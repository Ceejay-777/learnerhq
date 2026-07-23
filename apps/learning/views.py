from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Subject, Topic, TopicProgress, UserSubjectProgress
from apps.ai.exceptions import ProviderError
from .serializers import (
    CreateSubjectSerializer,
    EnrolledSubjectListSerializer,
    ExploreSubjectSerializer,
    GenerateQuizRequestSerializer,
    GenerateQuizResponseSerializer,
    LeaderboardEntrySerializer,
    LevelCheckResponseSerializer,
    NotificationFrequencySerializer,
    NotificationStatusSerializer,
    OthersLearningEntrySerializer,
    ResourceLinksViewedResponseSerializer,
    SubjectDetailSerializer,
    SubjectPreviewSerializer,
    SubjectSuggestionSerializer,
    SubmitQuizRequestSerializer,
    SubmitQuizResponseSerializer,
    TopicDetailSerializer,
    TopicLeaderboardEntrySerializer,
)
from .services import (
    _generate_subject_suggestions,
    add_subject_to_user,
    resolve_or_create_subject,
    check_level_progress,
    explore_subjects,
    generate_quiz,
    get_subject_detail,
    get_subject_preview,
    global_leaderboard,
    mark_resource_links_viewed,
    mark_subject_interest,
    others_learning,
    remove_subject_interest,
    set_notification_frequency,
    submit_quiz,
    topic_leaderboard,
)


class CheckLevelProgressView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Check level progression",
        request=None,
        responses={200: LevelCheckResponseSerializer},
    )
    def post(self, request, subject_id):
        subject = Subject.objects.get(id=subject_id)
        result = check_level_progress(request.user, subject)
        resp = {"action": result.action}
        if result.action == "level_up":
            resp["new_level_unlocked"] = result.new_level_unlocked
            resp["slots_available"] = result.slots_available
            resp["suggestions"] = result.suggestions
        elif result.action == "subject_completed":
            usp = UserSubjectProgress.objects.get(user=request.user, subject=subject)
            resp["needs_subject_selection"] = usp.needs_subject_selection
            resp["suggestions"] = result.suggestions
        return Response({"data": resp, "status": "success"})


class ExploreSubjectsView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="List explore subjects",
        responses={200: ExploreSubjectSerializer(many=True)},
    )
    def get(self, request):
        results = explore_subjects(request.user)
        return Response({"data": results, "status": "success"})


class MarkInterestView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Mark interest in a subject",
        request=None,
        responses={201: None},
    )
    def post(self, request, subject_id):
        subject = Subject.objects.get(id=subject_id)
        mark_subject_interest(request.user, subject)
        return Response({"data": {}, "status": "success"}, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Learning"],
        summary="Remove interest",
        request=None,
        responses={204: None},
    )
    def delete(self, request, subject_id):
        subject = Subject.objects.get(id=subject_id)
        remove_subject_interest(request.user, subject)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AddSubjectView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Add subject to user",
        request=None,
        responses={201: None, 400: None},
    )
    def post(self, request, subject_id):
        subject = Subject.objects.get(id=subject_id)
        try:
            add_subject_to_user(request.user, subject)
        except ValueError as e:
            return Response({"detail": str(e), "status": "error"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"data": {}, "status": "success"}, status=status.HTTP_201_CREATED)


class RemoveSubjectView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Remove subject from user",
        request=None,
        responses={204: None, 404: None},
    )
    def delete(self, request, subject_id):
        deleted, _ = UserSubjectProgress.objects.filter(
            user=request.user, subject_id=subject_id,
        ).delete()
        if not deleted:
            return Response({"detail": "Not found.", "status": "error"}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SubjectSuggestionsView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Get subject suggestions",
        responses={200: SubjectSuggestionSerializer(many=True)},
    )
    def get(self, request):
        suggestions = _generate_subject_suggestions(request.user)
        return Response({"data": suggestions, "status": "success"})


class SetNotificationFrequencyView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Set notification frequency",
        request=NotificationFrequencySerializer,
        responses={200: None, 400: None},
    )
    def patch(self, request, subject_id):
        subject = Subject.objects.get(id=subject_id)
        hours = request.data.get("frequency_hours")
        if hours is None:
            return Response({"detail": "frequency_hours is required", "status": "error"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            return Response({"detail": "frequency_hours must be an integer", "status": "error"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            set_notification_frequency(request.user, subject, hours)
        except ValueError as e:
            return Response({"detail": str(e), "status": "error"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"data": {}, "status": "success"})


class NotificationStatusView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Get notification status",
        responses={200: NotificationStatusSerializer, 404: None},
    )
    def get(self, request, subject_id):
        subject = Subject.objects.get(id=subject_id)
        try:
            usp = UserSubjectProgress.objects.get(user=request.user, subject=subject)
        except UserSubjectProgress.DoesNotExist:
            return Response({"detail": "Not found.", "status": "error"}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "data": {
                "frequency_hours": usp.notification_frequency_hours,
                "next_due_at": usp.next_due_at,
            },
            "status": "success",
        })


class LeaderboardView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Global leaderboard",
        responses={200: LeaderboardEntrySerializer(many=True)},
    )
    def get(self, request):
        data = global_leaderboard()
        return Response({"data": data, "status": "success"})


class TopicLeaderboardView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Topic leaderboard",
        responses={200: TopicLeaderboardEntrySerializer(many=True)},
    )
    def get(self, request, topic_id):
        data = topic_leaderboard(topic_id)
        return Response({"data": data, "status": "success"})


class OthersLearningView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Others learning this topic",
        responses={200: OthersLearningEntrySerializer(many=True)},
    )
    def get(self, request, topic_id):
        data = others_learning(topic_id, request.user)
        return Response({"data": data, "status": "success"})


class GenerateQuizView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Generate a quiz for a topic",
        request=GenerateQuizRequestSerializer,
        responses={200: GenerateQuizResponseSerializer, 400: None},
    )
    def post(self, request, topic_id):
        topic = Topic.objects.get(id=topic_id)
        quiz_type = request.data.get("quiz_type", "NORMAL")
        prior_missed_questions = request.data.get("prior_missed_questions")
        try:
            attempt = generate_quiz(request.user, topic, quiz_type, prior_missed_questions)
        except ValueError as e:
            return Response({"detail": str(e), "status": "error"}, status=status.HTTP_400_BAD_REQUEST)
        except ProviderError as e:
            return Response(
                {"detail": "The AI service is temporarily unavailable. Please try again shortly.", "status": "error"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({
            "data": {
                "id": attempt.id,
                "quiz_type": attempt.quiz_type,
                "attempt_number": attempt.attempt_number,
                "questions": attempt.questions,
                "total_points": attempt.total_points,
            },
            "status": "success",
        })


class SubmitQuizView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Submit answers for a quiz",
        request=SubmitQuizRequestSerializer,
        responses={200: SubmitQuizResponseSerializer, 400: None},
    )
    def post(self, request, topic_id):
        attempt_id = request.data.get("attempt_id")
        answers = request.data.get("answers")
        try:
            attempt = submit_quiz(attempt_id, answers)
        except ValueError as e:
            return Response({"detail": str(e), "status": "error"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "data": {
                "id": attempt.id,
                "passed": attempt.passed,
                "score": attempt.score,
                "total_points": attempt.total_points,
                "attempt_number": attempt.attempt_number,
                "quiz_type": attempt.quiz_type,
            },
            "status": "success",
        })


class MarkResourceLinksViewedView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Mark resource links as viewed",
        request=None,
        responses={200: ResourceLinksViewedResponseSerializer},
    )
    def post(self, request, topic_id):
        topic = Topic.objects.get(id=topic_id)
        tp = mark_resource_links_viewed(request.user, topic)
        return Response({
            "data": {
                "status": tp.status,
                "resource_links_viewed_at": tp.resource_links_viewed_at,
            },
            "status": "success",
        })


class CreateSubjectView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CreateSubjectSerializer

    @extend_schema(
        tags=["Learning"],
        summary="Create or resolve a subject by name",
        request=CreateSubjectSerializer,
        responses={200: None, 201: None, 300: None, 400: None},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"].strip()
        if not name:
            return Response({"detail": "Name is required.", "status": "error"}, status=status.HTTP_400_BAD_REQUEST)

        result = resolve_or_create_subject(name)

        if result.action == "narrow":
            return Response(
                {"data": {"action": "narrow", "suggestion": result.suggestion}, "status": "success"},
                status=status.HTTP_300_MULTIPLE_CHOICES,
            )

        try:
            add_subject_to_user(request.user, result.subject)
        except ValueError as e:
            return Response({"detail": str(e), "status": "error"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "data": {
                    "action": "resolved",
                    "subject": {"id": result.subject.id, "name": result.subject.name},
                },
                "status": "success",
            },
            status=status.HTTP_200_OK,
        )


class TopicDetailView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Get topic details and content",
        responses={200: TopicDetailSerializer},
    )
    def get(self, request, topic_id):
        topic = Topic.objects.select_related("subject").get(id=topic_id)
        return Response({
            "data": {
                "id": topic.id,
                "title": topic.title,
                "summary": topic.summary,
                "resource_links": topic.resource_links,
                "level": topic.level,
                "order": topic.order,
                "content_status": topic.content_status,
                "review_status": topic.review_status,
                "subject_id": topic.subject_id,
                "subject_name": topic.subject.name,
            },
            "status": "success",
        })


class EnrolledSubjectsView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="List enrolled subjects with progress",
        responses={200: EnrolledSubjectListSerializer(many=True)},
    )
    def get(self, request):
        from django.db.models import Count, Q
        from math import floor

        usps = (
            UserSubjectProgress.objects
            .filter(user=request.user)
            .select_related("subject")
            .annotate(
                topics_total=Count("subject__topics", distinct=True),
                topics_passed=Count(
                    "subject__topics__user_progress",
                    filter=Q(
                        subject__topics__user_progress__user=request.user,
                        subject__topics__user_progress__status__in=[
                            TopicProgress.Status.PASSED,
                            TopicProgress.Status.ADVANCED_PASSED,
                        ],
                    ),
                    distinct=True,
                ),
                topics_advanced_passed=Count(
                    "subject__topics__user_progress",
                    filter=Q(
                        subject__topics__user_progress__user=request.user,
                        subject__topics__user_progress__status=TopicProgress.Status.ADVANCED_PASSED,
                    ),
                    distinct=True,
                ),
            )
            .order_by("-created_at")
        )

        results = []
        for usp in usps:
            passed = usp.topics_passed if usp.topics_passed is not None else 0
            total = usp.topics_total if usp.topics_total is not None else 0
            pct = floor((passed / total) * 100) if total > 0 else 0
            results.append({
                "id": usp.subject_id,
                "name": usp.subject.name,
                "status": usp.status,
                "points": usp.points,
                "level_unlocked": usp.level_unlocked,
                "topics_total": total,
                "topics_passed": passed,
                "topics_advanced_passed": usp.topics_advanced_passed or 0,
                "percent_complete": pct,
                "created_at": usp.created_at,
                "notification_frequency_hours": usp.notification_frequency_hours,
                "next_due_at": usp.next_due_at,
            })
        return Response({"data": results, "status": "success"})


class SubjectDetailView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Get full subject detail with topics and progress",
        responses={200: SubjectDetailSerializer, 404: None},
    )
    def get(self, request, subject_id):
        try:
            result = get_subject_detail(request.user, subject_id)
        except UserSubjectProgress.DoesNotExist:
            return Response({"detail": "Not found.", "status": "error"}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "data": {
                "id": result.subject_id,
                "name": result.name,
                "status": result.status,
                "points": result.points,
                "level_unlocked": result.level_unlocked,
                "created_at": result.created_at,
                "notification_frequency_hours": result.notification_frequency_hours,
                "next_due_at": result.next_due_at,
                "levels": result.levels,
                "topics": result.topics,
            },
            "status": "success",
        })


class SubjectPreviewView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Learning"],
        summary="Preview a subject with its topic roadmap (no auth beyond login required)",
        responses={200: SubjectPreviewSerializer, 404: None},
    )
    def get(self, request, subject_id):
        try:
            result = get_subject_preview(request.user, subject_id)
        except Subject.DoesNotExist:
            return Response({"detail": "Not found.", "status": "error"}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "data": {
                "id": result.subject_id,
                "name": result.name,
                "enrollment_count": result.enrollment_count,
                "is_enrolled": result.is_enrolled,
                "is_completed": result.is_completed,
                "is_interested": result.is_interested,
                "levels": result.levels,
                "topics": result.topics,
            },
            "status": "success",
        })
