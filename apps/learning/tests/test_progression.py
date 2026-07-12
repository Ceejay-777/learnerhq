import pytest
from django.contrib.auth import get_user_model
from django.db import models
from apps.learning.models import (
    Subject, Topic, UserSubjectProgress, TopicProgress, UserInterest,
)
from apps.learning.services import (
    compute_level_threshold,
    check_level_progress,
    LevelCheckResult,
    get_available_slots,
    add_subject_to_user,
    explore_subjects,
    mark_subject_interest,
    remove_subject_interest,
)
from apps.learning.tasks import auto_select_subjects

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p")


@pytest.fixture
def subject():
    return Subject.objects.create(name="World War II")


@pytest.fixture
def subject2():
    return Subject.objects.create(name="The French Revolution")


class TestComputeLevelThreshold:

    def test_10_topics_80_percent(self):
        assert compute_level_threshold(10) == 8

    def test_5_topics_80_percent(self):
        assert compute_level_threshold(5) == 4

    def test_3_topics_all_but_one(self):
        assert compute_level_threshold(3) == 2

    def test_2_topics_all_but_one(self):
        assert compute_level_threshold(2) == 1

    def test_1_topic(self):
        assert compute_level_threshold(1) == 1

    def test_7_topics(self):
        assert compute_level_threshold(7) == 6

    def test_8_topics(self):
        assert compute_level_threshold(8) == 7

    def test_9_topics(self):
        assert compute_level_threshold(9) == 8


@pytest.mark.django_db
class TestLevelCheckProgress:

    def _level_topics(self, subject, level, count):
        next_order = (Topic.objects.filter(subject=subject).aggregate(m=models.Max("order"))["m"] or 0) + 1
        return [
            Topic.objects.create(subject=subject, title=f"L{level}-T{i}", level=level, order=next_order + j)
            for j, i in enumerate(range(1, count + 1))
        ]

    def _complete_topic(self, user, topic):
        TopicProgress.objects.create(user=user, topic=topic, status=TopicProgress.Status.PASSED)

    def test_no_progress_returns_none(self, user, subject):
        self._level_topics(subject, 1, 5)
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        result = check_level_progress(user, subject)
        assert result.action == "none"

    def test_insufficient_completions_returns_none(self, user, subject):
        topics = self._level_topics(subject, 1, 5)
        self._complete_topic(user, topics[0])
        self._complete_topic(user, topics[1])
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        result = check_level_progress(user, subject)
        assert result.action == "none"

    def test_level_up_1_to_2(self, user, subject):
        topics = self._level_topics(subject, 1, 5)
        for t in topics[:4]:
            self._complete_topic(user, t)
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        result = check_level_progress(user, subject)
        assert result.action == "level_up"
        assert result.new_level_unlocked == 2
        usp.refresh_from_db()
        assert usp.level_unlocked == 2

    def test_level_up_2_to_3(self, user, subject):
        self._level_topics(subject, 1, 3)
        self._level_topics(subject, 2, 5)
        usp = UserSubjectProgress.objects.create(user=user, subject=subject, level_unlocked=2)
        l2_topics = Topic.objects.filter(subject=subject, level=2)
        for t in l2_topics[:4]:
            self._complete_topic(user, t)
        result = check_level_progress(user, subject)
        assert result.action == "level_up"
        assert result.new_level_unlocked == 3

    def test_level_up_all_but_one_for_small_level(self, user, subject):
        topics = self._level_topics(subject, 1, 3)
        for t in topics[:2]:
            self._complete_topic(user, t)
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        result = check_level_progress(user, subject)
        assert result.action == "level_up"

    def test_subject_completed_at_level_3(self, user, subject):
        self._level_topics(subject, 1, 3)
        self._level_topics(subject, 2, 3)
        self._level_topics(subject, 3, 5)
        usp = UserSubjectProgress.objects.create(
            user=user, subject=subject, level_unlocked=3,
        )
        l3_topics = Topic.objects.filter(subject=subject, level=3)
        for t in l3_topics[:4]:
            self._complete_topic(user, t)
        result = check_level_progress(user, subject)
        assert result.action == "subject_completed"
        usp.refresh_from_db()
        assert usp.status == UserSubjectProgress.Status.COMPLETED
        assert usp.needs_subject_selection is True

    def test_mid_level_offers_slots_when_available(self, user, subject):
        self._level_topics(subject, 1, 5)
        for t in Topic.objects.filter(subject=subject, level=1)[:4]:
            self._complete_topic(user, t)
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        result = check_level_progress(user, subject)
        assert result.action == "level_up"
        assert result.slots_available == 4  # 0 active, max 5

    def test_mid_level_no_offer_when_at_cap(self, user, subject):
        self._level_topics(subject, 1, 5)
        for t in Topic.objects.filter(subject=subject, level=1)[:4]:
            self._complete_topic(user, t)
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        for i in range(5):
            s = Subject.objects.create(name=f"FillSubject{i}")
            UserSubjectProgress.objects.create(user=user, subject=s)
        result = check_level_progress(user, subject)
        assert result.action == "level_up"
        assert result.slots_available == 0

    def test_mid_level_slots_count_active_only(self, user, subject):
        self._level_topics(subject, 1, 5)
        for t in Topic.objects.filter(subject=subject, level=1)[:4]:
            self._complete_topic(user, t)
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        s = Subject.objects.create(name="CompletedSubject")
        UserSubjectProgress.objects.create(
            user=user, subject=s, status=UserSubjectProgress.Status.COMPLETED,
        )
        result = check_level_progress(user, subject)
        assert result.slots_available == 4  # completed doesn't count


@pytest.mark.django_db
class TestSlotManagement:

    def test_get_available_slots_empty(self, user):
        assert get_available_slots(user) == 5

    def test_get_available_slots_with_active(self, user):
        s1 = Subject.objects.create(name="S1")
        s2 = Subject.objects.create(name="S2")
        UserSubjectProgress.objects.create(user=user, subject=s1)
        UserSubjectProgress.objects.create(user=user, subject=s2)
        assert get_available_slots(user) == 3

    def test_get_available_slots_completed_dont_count(self, user):
        s1 = Subject.objects.create(name="S1")
        s2 = Subject.objects.create(name="S2")
        UserSubjectProgress.objects.create(user=user, subject=s1)
        UserSubjectProgress.objects.create(
            user=user, subject=s2, status=UserSubjectProgress.Status.COMPLETED,
        )
        assert get_available_slots(user) == 4

    def _with_topics(self, subject, count=3):
        for i in range(count):
            Topic.objects.create(subject=subject, title=f"T{i}", level=1, order=i + 1)

    def test_add_subject_success(self, user, subject):
        self._with_topics(subject)
        usp = add_subject_to_user(user, subject)
        assert usp.user == user
        assert usp.subject == subject
        assert usp.status == UserSubjectProgress.Status.ACTIVE
        assert UserSubjectProgress.objects.filter(user=user, subject=subject).exists()

    def test_add_subject_at_cap_raises(self, user):
        for i in range(5):
            s = Subject.objects.create(name=f"S{i}")
            UserSubjectProgress.objects.create(user=user, subject=s)
        with pytest.raises(ValueError, match="limit of 5"):
            add_subject_to_user(user, Subject.objects.create(name="Extra"))

    def test_add_subject_duplicate_returns_existing(self, user, subject):
        self._with_topics(subject)
        usp1 = add_subject_to_user(user, subject)
        usp2 = add_subject_to_user(user, subject)
        assert usp1.id == usp2.id

    def test_add_subject_clears_needs_selection(self, user):
        s1 = Subject.objects.create(name="S1")
        s2 = Subject.objects.create(name="S2")
        self._with_topics(s2)
        UserSubjectProgress.objects.create(
            user=user, subject=s1,
            status=UserSubjectProgress.Status.COMPLETED,
            needs_subject_selection=True,
        )
        add_subject_to_user(user, s2)
        usp1 = UserSubjectProgress.objects.get(user=user, subject=s1)
        assert usp1.needs_subject_selection is False

    def test_add_generates_roadmap_if_needed(self, user, subject):
        from unittest.mock import patch
        with patch("apps.learning.services.generate_roadmap") as mock_gen:
            add_subject_to_user(user, subject)
            mock_gen.assert_called_once_with(subject)


@pytest.mark.django_db
class TestExploreSubjects:

    def test_returns_all_subjects(self, user):
        s1 = Subject.objects.create(name="S1")
        s2 = Subject.objects.create(name="S2")
        Topic.objects.create(subject=s1, title="T1", level=1, order=1)
        Topic.objects.create(subject=s2, title="T2", level=1, order=1)
        results = explore_subjects(user)
        assert len(results) == 2

    def test_skips_subjects_without_topics(self, user):
        Subject.objects.create(name="Empty")
        s2 = Subject.objects.create(name="HasTopics")
        Topic.objects.create(subject=s2, title="T1", level=1, order=1)
        results = explore_subjects(user)
        assert len(results) == 1
        assert results[0]["name"] == "HasTopics"

    def test_annotates_enrollment(self, user):
        s = Subject.objects.create(name="S1")
        Topic.objects.create(subject=s, title="T1", level=1, order=1)
        UserSubjectProgress.objects.create(user=user, subject=s)
        results = explore_subjects(user)
        assert results[0]["is_enrolled"] is True

    def test_annotates_completed(self, user):
        s = Subject.objects.create(name="S1")
        Topic.objects.create(subject=s, title="T1", level=1, order=1)
        UserSubjectProgress.objects.create(
            user=user, subject=s, status=UserSubjectProgress.Status.COMPLETED,
        )
        results = explore_subjects(user)
        assert results[0]["is_completed"] is True

    def test_annotates_interest(self, user):
        s = Subject.objects.create(name="S1")
        Topic.objects.create(subject=s, title="T1", level=1, order=1)
        UserInterest.objects.create(user=user, subject=s)
        results = explore_subjects(user)
        assert results[0]["is_interested"] is True

    def test_orders_by_popularity(self, user):
        s1 = Subject.objects.create(name="Popular")
        s2 = Subject.objects.create(name="LessPopular")
        Topic.objects.create(subject=s1, title="T1", level=1, order=1)
        Topic.objects.create(subject=s2, title="T2", level=1, order=1)
        user2 = User.objects.create_user(email="b@b.com", password="p")
        UserSubjectProgress.objects.create(user=user2, subject=s1)
        results = explore_subjects(user)
        assert results[0]["name"] == "Popular"


@pytest.mark.django_db
class TestInterestTracking:

    def test_mark_interest_creates(self, user, subject):
        interest = mark_subject_interest(user, subject)
        assert interest.user == user
        assert interest.subject == subject
        assert UserInterest.objects.filter(user=user, subject=subject).exists()

    def test_mark_interest_idempotent(self, user, subject):
        i1 = mark_subject_interest(user, subject)
        i2 = mark_subject_interest(user, subject)
        assert i1.id == i2.id

    def test_remove_interest(self, user, subject):
        mark_subject_interest(user, subject)
        remove_subject_interest(user, subject)
        assert not UserInterest.objects.filter(user=user, subject=subject).exists()

    def test_remove_nonexistent_interest_no_error(self, user, subject):
        remove_subject_interest(user, subject)


@pytest.mark.django_db
class TestCheckNeedsSelection:

    def test_no_pending_selection(self, user):
        from apps.learning.services import check_needs_selection
        result = check_needs_selection(user)
        assert result is None

    def test_has_pending_selection(self, user):
        from apps.learning.services import check_needs_selection
        s = Subject.objects.create(name="S1")
        UserSubjectProgress.objects.create(
            user=user, subject=s,
            status=UserSubjectProgress.Status.COMPLETED,
            needs_subject_selection=True,
        )
        result = check_needs_selection(user)
        assert result is not None
        assert result["subject_name"] == "S1"
        assert "suggestions" in result


@pytest.mark.django_db
class TestAutoSelectSubjects:

    def test_opted_in_and_idle_gets_enrolled(self, user):
        from django.utils import timezone
        from datetime import timedelta

        user.preferences.auto_select_subjects_enabled = True
        user.preferences.save()
        old = timezone.now() - timedelta(hours=25)
        User.objects.filter(id=user.id).update(last_login=old)

        s1 = Subject.objects.create(name="Completed")
        Topic.objects.create(subject=s1, title="T", level=1, order=1)
        s2 = Subject.objects.create(name="Available")
        Topic.objects.create(subject=s2, title="T", level=1, order=1)

        usp = UserSubjectProgress.objects.create(
            user=user, subject=s1,
            status=UserSubjectProgress.Status.COMPLETED,
            needs_subject_selection=True,
        )

        auto_select_subjects()

        usp.refresh_from_db()
        assert usp.needs_subject_selection is False
        assert UserSubjectProgress.objects.filter(
            user=user, subject=s2, status=UserSubjectProgress.Status.ACTIVE,
        ).exists()

    def test_not_opted_in_never_enrolled(self, user):
        from django.utils import timezone
        from datetime import timedelta

        old = timezone.now() - timedelta(hours=25)
        User.objects.filter(id=user.id).update(last_login=old)

        s1 = Subject.objects.create(name="S1")
        Topic.objects.create(subject=s1, title="T", level=1, order=1)
        s2 = Subject.objects.create(name="S2")
        Topic.objects.create(subject=s2, title="T", level=1, order=1)

        usp = UserSubjectProgress.objects.create(
            user=user, subject=s1,
            status=UserSubjectProgress.Status.COMPLETED,
            needs_subject_selection=True,
        )

        auto_select_subjects()

        usp.refresh_from_db()
        assert usp.needs_subject_selection is True

    def test_active_usp_blocks_auto_enroll(self, user):
        from django.utils import timezone
        from datetime import timedelta

        user.preferences.auto_select_subjects_enabled = True
        user.preferences.save()
        old = timezone.now() - timedelta(hours=25)
        User.objects.filter(id=user.id).update(last_login=old)

        s1 = Subject.objects.create(name="Active")
        Topic.objects.create(subject=s1, title="T", level=1, order=1)
        UserSubjectProgress.objects.create(
            user=user, subject=s1,
            status=UserSubjectProgress.Status.ACTIVE,
            needs_subject_selection=True,
        )

        auto_select_subjects()

        usp = UserSubjectProgress.objects.get(user=user, subject=s1)
        assert usp.needs_subject_selection is True

    def test_not_idle_not_enrolled(self, user):
        user.preferences.auto_select_subjects_enabled = True
        user.preferences.save()

        s1 = Subject.objects.create(name="S1")
        Topic.objects.create(subject=s1, title="T", level=1, order=1)
        s2 = Subject.objects.create(name="S2")
        Topic.objects.create(subject=s2, title="T", level=1, order=1)

        usp = UserSubjectProgress.objects.create(
            user=user, subject=s1,
            status=UserSubjectProgress.Status.COMPLETED,
            needs_subject_selection=True,
        )

        auto_select_subjects()

        usp.refresh_from_db()
        assert usp.needs_subject_selection is True

    def test_enrolls_once_clears_all_flags(self, user):
        from django.utils import timezone
        from datetime import timedelta

        user.preferences.auto_select_subjects_enabled = True
        user.preferences.save()
        old = timezone.now() - timedelta(hours=25)
        User.objects.filter(id=user.id).update(last_login=old)

        s1 = Subject.objects.create(name="S1")
        Topic.objects.create(subject=s1, title="T", level=1, order=1)
        s2 = Subject.objects.create(name="S2")
        Topic.objects.create(subject=s2, title="T", level=1, order=1)
        s3 = Subject.objects.create(name="Available")
        Topic.objects.create(subject=s3, title="T", level=1, order=1)

        usp1 = UserSubjectProgress.objects.create(
            user=user, subject=s1,
            status=UserSubjectProgress.Status.COMPLETED,
            needs_subject_selection=True,
        )
        usp2 = UserSubjectProgress.objects.create(
            user=user, subject=s2,
            status=UserSubjectProgress.Status.COMPLETED,
            needs_subject_selection=True,
        )

        auto_select_subjects()

        usp1.refresh_from_db()
        usp2.refresh_from_db()
        assert usp1.needs_subject_selection is False
        assert usp2.needs_subject_selection is False
        assert UserSubjectProgress.objects.filter(
            user=user, subject=s3, status=UserSubjectProgress.Status.ACTIVE,
        ).exists()

    def test_skips_when_no_suggestions(self, user):
        from django.utils import timezone
        from datetime import timedelta

        user.preferences.auto_select_subjects_enabled = True
        user.preferences.save()
        old = timezone.now() - timedelta(hours=25)
        User.objects.filter(id=user.id).update(last_login=old)

        s1 = Subject.objects.create(name="Lonely")
        usp = UserSubjectProgress.objects.create(
            user=user, subject=s1,
            status=UserSubjectProgress.Status.COMPLETED,
            needs_subject_selection=True,
        )

        auto_select_subjects()

        usp.refresh_from_db()
        assert usp.needs_subject_selection is True
