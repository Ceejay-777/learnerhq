import pytest
from django.contrib.auth import get_user_model
from apps.learning.models import Subject, Topic, UserSubjectProgress, TopicProgress

User = get_user_model()


@pytest.fixture
def subject():
    return Subject.objects.create(name="World War II")


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p")


TOPIC_DATA = [
    {"title": "Causes of WW2", "level": 1},
    {"title": "Major Battles", "level": 1},
    {"title": "Key Figures", "level": 1},
    {"title": "Technology & Warfare", "level": 2},
    {"title": "Home Front", "level": 2},
    {"title": "Aftermath & Legacy", "level": 3},
]


@pytest.mark.django_db
class TestAppendRoadmapTopics:

    def test_append_to_empty_roadmap(self, subject):
        from apps.learning.services import append_roadmap_topics

        topics = append_roadmap_topics(subject, TOPIC_DATA)
        assert len(topics) == 6
        for i, t in enumerate(topics):
            assert t.order == i + 1
            assert t.subject_id == subject.id

    def test_append_to_existing_roadmap(self, subject):
        from apps.learning.services import append_roadmap_topics

        Topic.objects.create(subject=subject, title="Existing", level=1, order=1)
        topics = append_roadmap_topics(subject, TOPIC_DATA)
        assert len(topics) == 6
        for i, t in enumerate(topics):
            assert t.order == i + 2

    def test_append_does_not_affect_user_progress(self, subject, user):
        from apps.learning.services import append_roadmap_topics

        existing = Topic.objects.create(subject=subject, title="Existing", level=1, order=1)
        TopicProgress.objects.create(user=user, topic=existing, status=TopicProgress.Status.PASSED)

        append_roadmap_topics(subject, TOPIC_DATA)

        tp = TopicProgress.objects.get(user=user, topic=existing)
        assert tp.status == TopicProgress.Status.PASSED

    def test_append_does_not_mark_bonus(self, subject, user):
        from apps.learning.services import append_roadmap_topics

        existing = Topic.objects.create(subject=subject, title="Existing", level=1, order=1)
        TopicProgress.objects.create(user=user, topic=existing, status=TopicProgress.Status.PASSED)

        appended = append_roadmap_topics(subject, TOPIC_DATA)
        for t in appended:
            assert not TopicProgress.objects.filter(user=user, topic=t).exists()


@pytest.mark.django_db
class TestInsertRoadmapTopics:

    def test_insert_shifts_existing_orders(self, subject):
        from apps.learning.services import insert_roadmap_topics

        t1 = Topic.objects.create(subject=subject, title="A", level=1, order=1)
        t2 = Topic.objects.create(subject=subject, title="B", level=1, order=2)
        t3 = Topic.objects.create(subject=subject, title="C", level=1, order=3)

        inserted = insert_roadmap_topics(subject, insert_before_order=2, topics_data=[
            {"title": "Inserted", "level": 1},
        ])
        assert len(inserted) == 1
        assert inserted[0].order == 2

        t1.refresh_from_db()
        t2.refresh_from_db()
        t3.refresh_from_db()
        assert t1.order == 1
        assert t2.order == 3
        assert t3.order == 4

    def test_insert_multiple_topics(self, subject):
        from apps.learning.services import insert_roadmap_topics

        Topic.objects.create(subject=subject, title="A", level=1, order=1)
        Topic.objects.create(subject=subject, title="B", level=1, order=2)

        inserted = insert_roadmap_topics(subject, insert_before_order=2, topics_data=[
            {"title": "X", "level": 1},
            {"title": "Y", "level": 1},
        ])
        assert len(inserted) == 2
        assert inserted[0].order == 2
        assert inserted[1].order == 3

        old_b = Topic.objects.get(title="B")
        assert old_b.order == 4

    def test_insert_at_beginning(self, subject):
        from apps.learning.services import insert_roadmap_topics

        t1 = Topic.objects.create(subject=subject, title="A", level=1, order=1)
        inserted = insert_roadmap_topics(subject, insert_before_order=1, topics_data=[
            {"title": "NewFirst", "level": 1},
        ])
        assert inserted[0].order == 1
        t1.refresh_from_db()
        assert t1.order == 2

    def test_insert_at_end(self, subject):
        from apps.learning.services import insert_roadmap_topics

        t1 = Topic.objects.create(subject=subject, title="A", level=1, order=1)
        inserted = insert_roadmap_topics(subject, insert_before_order=2, topics_data=[
            {"title": "NewLast", "level": 1},
        ])
        assert inserted[0].order == 2
        t1.refresh_from_db()
        assert t1.order == 1

    def test_insert_does_not_touch_topic_progress_ids(self, subject, user):
        from apps.learning.services import insert_roadmap_topics

        t1 = Topic.objects.create(subject=subject, title="A", level=1, order=1)
        tp = TopicProgress.objects.create(user=user, topic=t1, status=TopicProgress.Status.PASSED)

        insert_roadmap_topics(subject, insert_before_order=2, topics_data=[
            {"title": "Inserted", "level": 1},
        ])

        tp.refresh_from_db()
        assert tp.status == TopicProgress.Status.PASSED
        assert tp.topic_id == t1.id

    def test_insert_behind_completed_user_marks_bonus(self, subject, user):
        from apps.learning.services import insert_roadmap_topics

        for title, level in [("T1", 1), ("T2", 1), ("T3", 1)]:
            t = Topic.objects.create(subject=subject, title=title, level=level, order=Topic.objects.filter(subject=subject).count() + 1)
            if title in ("T1", "T2"):
                TopicProgress.objects.create(user=user, topic=t, status=TopicProgress.Status.PASSED)

        inserted = insert_roadmap_topics(subject, insert_before_order=2, topics_data=[
            {"title": "BonusTopic", "level": 1},
        ])

        tp = TopicProgress.objects.get(user=user, topic=inserted[0])
        assert tp.is_bonus is True

    def test_insert_ahead_of_user_not_bonus(self, subject, user):
        from apps.learning.services import insert_roadmap_topics

        t1 = Topic.objects.create(subject=subject, title="T1", level=1, order=1)
        TopicProgress.objects.create(user=user, topic=t1, status=TopicProgress.Status.PASSED)
        t2 = Topic.objects.create(subject=subject, title="T2", level=1, order=2)

        inserted = insert_roadmap_topics(subject, insert_before_order=3, topics_data=[
            {"title": "FutureTopic", "level": 1},
        ])

        assert not TopicProgress.objects.filter(user=user, topic=inserted[0]).exists()

    def test_insert_behind_with_no_progress_not_bonus(self, subject, user):
        from apps.learning.services import insert_roadmap_topics

        Topic.objects.create(subject=subject, title="T1", level=1, order=1)

        inserted = insert_roadmap_topics(subject, insert_before_order=1, topics_data=[
            {"title": "NewFirst", "level": 1},
        ])

        assert not TopicProgress.objects.filter(user=user, topic=inserted[0]).exists()

    def test_multiple_users_different_bonus_status(self, subject):
        from apps.learning.services import insert_roadmap_topics

        user1 = User.objects.create_user(email="u1@b.com", password="p")
        user2 = User.objects.create_user(email="u2@b.com", password="p")

        topics = []
        for title in ["T1", "T2", "T3", "T4"]:
            t = Topic.objects.create(subject=subject, title=title, level=1, order=len(topics) + 1)
            topics.append(t)

        # user1 completed T1, T2 (position at order 2)
        TopicProgress.objects.create(user=user1, topic=topics[0], status=TopicProgress.Status.PASSED)
        TopicProgress.objects.create(user=user1, topic=topics[1], status=TopicProgress.Status.PASSED)

        # user2 completed T1, T2, T3, T4 (position at order 4)
        for t in topics:
            TopicProgress.objects.create(user=user2, topic=t, status=TopicProgress.Status.PASSED)

        inserted = insert_roadmap_topics(subject, insert_before_order=3, topics_data=[
            {"title": "Middle", "level": 1},
        ])

        tp2 = TopicProgress.objects.get(user=user2, topic=inserted[0])
        assert tp2.is_bonus is True   # behind position 4, inserted at 3 → behind

        assert not TopicProgress.objects.filter(user=user1, topic=inserted[0]).exists()

    def test_completed_level_stays_completed_after_insert_behind(self, subject, user):
        from apps.learning.services import insert_roadmap_topics

        for i in range(3):
            Topic.objects.create(subject=subject, title=f"T{i+1}", level=1, order=i + 1)

        usp = UserSubjectProgress.objects.create(
            user=user, subject=subject,
            status=UserSubjectProgress.Status.ACTIVE,
            level_unlocked=2,
        )

        insert_roadmap_topics(subject, insert_before_order=2, topics_data=[
            {"title": "Bonus", "level": 1},
        ])

        usp.refresh_from_db()
        assert usp.status == UserSubjectProgress.Status.ACTIVE
        assert usp.level_unlocked == 2

    def test_completed_subject_stays_completed(self, subject, user):
        from apps.learning.services import insert_roadmap_topics

        for i in range(3):
            Topic.objects.create(subject=subject, title=f"T{i+1}", level=1, order=i + 1)

        UserSubjectProgress.objects.create(
            user=user, subject=subject,
            status=UserSubjectProgress.Status.COMPLETED,
            level_unlocked=3,
        )

        insert_roadmap_topics(subject, insert_before_order=2, topics_data=[
            {"title": "Bonus", "level": 1},
        ])

        usp = UserSubjectProgress.objects.get(user=user, subject=subject)
        assert usp.status == UserSubjectProgress.Status.COMPLETED

    def test_bonus_completion_counts_in_stats(self, subject, user):
        from apps.learning.services import insert_roadmap_topics

        t1 = Topic.objects.create(subject=subject, title="T1", level=1, order=1)
        TopicProgress.objects.create(user=user, topic=t1, status=TopicProgress.Status.PASSED)

        inserted = insert_roadmap_topics(subject, insert_before_order=1, topics_data=[
            {"title": "Bonus", "level": 1},
        ])

        tp = TopicProgress.objects.get(user=user, topic=inserted[0])
        tp.status = TopicProgress.Status.PASSED
        tp.points = 100
        tp.save()

        passed_tp = TopicProgress.objects.filter(user=user, status=TopicProgress.Status.PASSED)
        assert passed_tp.count() == 2
        total_points = sum(tp.points for tp in passed_tp)
        assert total_points == 100  # existing has 0, bonus has 100


@pytest.mark.django_db
class TestUpdateRoadmapTopic:

    def test_update_topic_title(self, subject):
        from apps.learning.services import update_roadmap_topic

        topic = Topic.objects.create(subject=subject, title="Old", level=1, order=1)
        updated = update_roadmap_topic(topic.id, title="New Title")
        assert updated.title == "New Title"
        assert updated.level == 1

    def test_update_topic_level(self, subject):
        from apps.learning.services import update_roadmap_topic

        topic = Topic.objects.create(subject=subject, title="Old", level=1, order=1)
        updated = update_roadmap_topic(topic.id, level=2)
        assert updated.title == "Old"
        assert updated.level == 2

    def test_update_topic_title_and_level(self, subject):
        from apps.learning.services import update_roadmap_topic

        topic = Topic.objects.create(subject=subject, title="Old", level=1, order=1)
        updated = update_roadmap_topic(topic.id, title="New", level=3)
        assert updated.title == "New"
        assert updated.level == 3

    def test_update_does_not_affect_user_progress(self, subject, user):
        from apps.learning.services import update_roadmap_topic

        topic = Topic.objects.create(subject=subject, title="Old", level=1, order=1)
        TopicProgress.objects.create(user=user, topic=topic, status=TopicProgress.Status.PASSED)

        update_roadmap_topic(topic.id, title="New")
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.status == TopicProgress.Status.PASSED

    def test_update_nonexistent_topic_raises(self, subject):
        from apps.learning.services import update_roadmap_topic

        with pytest.raises(Topic.DoesNotExist):
            update_roadmap_topic(99999, title="Nope")


@pytest.mark.django_db
class TestUpdateRoadmapCombined:

    def test_append_and_insert_and_update(self, subject, user):
        from apps.learning.services import update_roadmap

        t = Topic.objects.create(subject=subject, title="Original", level=1, order=1)

        result = update_roadmap(
            subject,
            append=[{"title": "Appended", "level": 2}],
            insert={"before_order": 1, "topics": [{"title": "Inserted", "level": 1}]},
            update={"topic_id": t.id, "title": "Updated Original"},
        )

        assert len(result["appended"]) == 1
        assert result["appended"][0].order == 3  # after insert shifts
        assert len(result["inserted"]) == 1
        assert result["inserted"][0].order == 1
        assert result["updated"].title == "Updated Original"

        all_topics = list(Topic.objects.filter(subject=subject).order_by("order"))
        assert len(all_topics) == 3
        assert all_topics[0].title == "Inserted"
        assert all_topics[1].title == "Updated Original"
        assert all_topics[2].title == "Appended"

    def test_combined_preserves_user_progress(self, subject, user):
        from apps.learning.services import update_roadmap

        t1 = Topic.objects.create(subject=subject, title="A", level=1, order=1)
        TopicProgress.objects.create(user=user, topic=t1, status=TopicProgress.Status.PASSED)

        update_roadmap(
            subject,
            insert={"before_order": 1, "topics": [{"title": "New", "level": 1}]},
        )

        tp = TopicProgress.objects.get(user=user, topic=t1)
        assert tp.status == TopicProgress.Status.PASSED

    def test_combined_empty_operations(self, subject):
        from apps.learning.services import update_roadmap

        result = update_roadmap(subject)
        assert result == {"appended": [], "inserted": [], "updated": None}
