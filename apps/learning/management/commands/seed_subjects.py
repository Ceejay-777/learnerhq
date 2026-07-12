import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.learning.services import add_subject_to_user, canonicalize_subject

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


class Command(BaseCommand):
    help = "Seed the database with initial subjects"

    def handle(self, *args, **options):
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

        self.stdout.write(f"Using seed user: {user.email} (id={user.id})")

        success = 0
        skipped = 0
        errors = 0

        for name in SUBJECTS:
            try:
                result = canonicalize_subject(name)
                if result.action == "narrow":
                    self.stdout.write(f"  ~ {name}: needs narrowing — {result.suggestion}")
                    skipped += 1
                    continue
                add_subject_to_user(user, result.subject)
                self.stdout.write(self.style.SUCCESS(f"  ✓ {result.subject.name}"))
                success += 1
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  ✗ {name}: {e}"))
                errors += 1

        self.stdout.write(f"\nDone: {success} created, {skipped} skipped, {errors} errors")
