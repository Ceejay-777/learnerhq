import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.learning.models import (
    QuizAttempt,
    Subject,
    Topic,
    TopicProgress,
    UserInterest,
    UserSubjectProgress,
)

PASSWORD = "SeedPass123!"
DOMAIN = "seed.learnerhq.test"
MAIN_USERS = [("demo1", "Demo One"), ("demo2", "Demo Two")]
NPC_COUNT = 25

FIRST = ["Ada", "Tunde", "Mei", "Lucas", "Amara", "Noah", "Priya", "Diego", "Zainab", "Kofi", "Sofia", "Hiro",
         "Chioma", "Liam", "Fatima", "Mateo", "Ife", "Anya", "Omar", "Grace", "Yusuf", "Elena", "Kwame", "Nia", "Jun"]
LAST = ["Okafor", "Chen", "Silva", "Patel", "Adeyemi", "Kim", "Novak", "Mensah", "Garcia", "Ibrahim", "Tanaka", "Walker"]
BIOS = ["Learning in public.", "Career switcher, 30 mins a day.", "Curious about everything.", "Weekend tinkerer.",
        "Grad student procrastinating productively.", "Here for the streaks.", ""]

# Every seeded quiz attempt reuses this one question.
SAMPLE_QUESTIONS = [{
    "question": "Sample question?",
    "options": ["a) One", "b) Two", "c) Three", "d) Four"],
    "correct_index": 1,
    "explanation": "Sample seeded data.",
}]


class Command(BaseCommand):
    help = (
        "Seed demo users (2 main + NPCs) with enrollments, progress, quizzes and interests "
        "on existing subjects. Safe to re-run."
    )

    def handle(self, *args, **options):
        self.rng = random.Random(42)
        self.now = timezone.now()
        self.User = get_user_model()

        self.subjects = list(Subject.objects.filter(roadmap_status="READY").order_by("id"))
        if not self.subjects:
            raise CommandError("No subjects with a READY roadmap. Create subjects first (e.g. seed_subjects).")
        self.topics = {s.id: list(Topic.objects.filter(subject=s).order_by("order")) for s in self.subjects}

        mains = []
        for i, (slug, name) in enumerate(MAIN_USERS):
            picks = [self.subjects[(i + j) % len(self.subjects)] for j in range(min(3, len(self.subjects)))]
            mains.append(self._seed_user(slug, name, picks, deep=True))
        hot = {s for u in mains for s in self._enrolled(u)}

        for n in range(1, NPC_COUNT + 1):
            picks = {self.rng.choice(sorted(hot, key=lambda s: s.id))}
            for _ in range(self.rng.randint(0, 3)):
                picks.add(self.rng.choice(self.subjects))
            self._seed_user(f"npc{n:02d}", f"{FIRST[n - 1]} {self.rng.choice(LAST)}", list(picks), deep=False)

        self.stdout.write(self.style.SUCCESS("\nMain users (password for both: %s)" % PASSWORD))
        for u in mains:
            subs = ", ".join(s.name for s in self._enrolled(u))
            self.stdout.write(f"  {u.email}  [{subs}]")

    def _enrolled(self, user):
        return [p.subject for p in user.subject_progress.select_related("subject")]

    @transaction.atomic
    def _seed_user(self, slug, name, picks, deep):
        user, created = self.User.objects.get_or_create(
            email=f"{slug}@{DOMAIN}",
            defaults={"display_name": name, "bio": self.rng.choice(BIOS)},
        )
        if created:
            user.set_password(PASSWORD)
            user.save(update_fields=["password"])
        if user.subject_progress.exists():
            return user  # already seeded

        for s in picks:
            self._enroll(user, s, self.rng.randint(8, 16) if deep else self.rng.randint(2, 14))
        enrolled = {s.id for s in picks}
        others = [s for s in self.subjects if s.id not in enrolled]
        for s in self.rng.sample(others, min(2, len(others))):
            UserInterest.objects.get_or_create(user=user, subject=s)
        return user

    def _enroll(self, user, subject, depth):
        rng, now = self.rng, self.now
        usp = UserSubjectProgress.objects.create(
            user=user,
            subject=subject,
            notification_frequency_hours=rng.choice([6, 12, 24]),
            next_due_at=now + timedelta(hours=rng.randint(1, 24)),
        )
        topics = self.topics[subject.id][:depth]
        total = 0
        for i, t in enumerate(topics):
            status = rng.choices(["ADVANCED_PASSED", "PASSED"], [0.35, 0.65])[0]
            if i == len(topics) - 1:
                status = rng.choice(["READING", "QUIZ_READY", "PASSED"])
            done = status in ("PASSED", "ADVANCED_PASSED")
            pts = normal_attempts = advanced_attempts = 0
            if done:
                fail_first = rng.random() < 0.3
                for n in range(1, (2 if fail_first else 1) + 1):
                    ok = not (fail_first and n == 1)
                    score = rng.randint(65, 100) if ok else rng.randint(20, 55)
                    self._attempt(user, t, "NORMAL", n, score, 100, ok)
                    normal_attempts = n
                    if ok:
                        pts = score
                if status == "ADVANCED_PASSED":
                    score = rng.randint(120, 200)
                    self._attempt(user, t, "ADVANCED", 1, score, 200, True)
                    advanced_attempts = 1
                    pts += score
            TopicProgress.objects.create(
                user=user,
                topic=t,
                status=status,
                points=pts,
                normal_quiz_attempts=normal_attempts,
                advanced_quiz_attempts=advanced_attempts,
                resource_links_viewed_at=now - timedelta(days=rng.randint(0, 20)) if status != "READING" else None,
                completed_at=now - timedelta(days=rng.randint(0, 20)) if done else None,
            )
            total += pts
        # auto_now_add ignores passed values, so backdate through update()
        UserSubjectProgress.objects.filter(pk=usp.pk).update(
            points=total,
            level_unlocked=topics[-1].level if topics else 1,
            created_at=now - timedelta(days=rng.randint(1, 60)),
        )

    def _attempt(self, user, topic, quiz_type, number, score, total, passed):
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=quiz_type, attempt_number=number,
            questions=SAMPLE_QUESTIONS, answers=[self.rng.randint(0, 3)],
            score=score, total_points=total, passed=passed,
        )
