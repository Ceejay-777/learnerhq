import logging
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.learning.services import add_subject_to_user, resolve_or_create_subject

logger = logging.getLogger(__name__)

SUBJECTS = [
    "American Football",
    "Python for Beginners",
    "AP Calculus AB",
    "World War II",
    "Creative Writing",
    "Spanish for Beginners",
    "Machine Learning",
    "Digital Photography",
    "Acoustic Guitar",
    "Yoga for Beginners",
    "Personal Finance",
    "Cognitive Psychology",
    "Ancient Egypt",
    "Wine Basics",
    "Film History",
]

RETRY_DELAY = 60


class Command(BaseCommand):
    help = "Seed the database with initial subjects"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=3,
            help="Number of subjects to seed (default: 3, use 0 for all)",
        )

    def handle(self, *args, **options):
        count = options["count"]
        subjects = SUBJECTS[:count] if count > 0 else SUBJECTS[:]

        User = get_user_model()
        user, created = User.objects.get_or_create(
            email="seed@learnerhq.dev",
            defaults={
                "first_name": "Seed",
                "last_name": "Bot",
            },
        )
        if created:
            user.set_unusable_password()
            user.save()

        self.stdout.write(f"Seeding {len(subjects)} subjects as {user.email} (id={user.id})")

        success = 0
        skipped = 0
        errors = 0

        for i, name in enumerate(subjects, 1):
            self.stdout.write(f"[{i}/{len(subjects)}] {name}...", ending=" ")
            self.stdout.flush()

            for attempt in range(3):
                try:
                    result = resolve_or_create_subject(name)
                    if result.action == "narrow":
                        self.stdout.write(self.style.WARNING(f"narrow — {result.suggestion}"))
                        skipped += 1
                        break
                    add_subject_to_user(user, result.subject)
                    self.stdout.write(self.style.SUCCESS("done"))
                    success += 1
                    break
                except Exception as e:
                    if attempt < 2 and getattr(e, 'recoverable', False):
                        self.stdout.write(self.style.WARNING(f"rate limited, retrying in {RETRY_DELAY}s..."))
                        time.sleep(RETRY_DELAY)
                    else:
                        self.stdout.write(self.style.ERROR(f"failed"))
                        self.stderr.write(self.style.ERROR(f"  {name}: {e}"))
                        errors += 1
                        break

        self.stdout.write(f"\nDone: {success} seeded, {skipped} skipped, {errors} errors")
