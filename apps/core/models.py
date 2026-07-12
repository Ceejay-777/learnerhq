from django.contrib.auth.models import AbstractUser, UserManager as BaseUserManager
from django.db import models
from django.dispatch import receiver
from django.db.models.signals import post_save


class UserManager(BaseUserManager):
    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True, max_length=254)
    display_name = models.CharField(max_length=100, blank=True, default="")
    avatar = models.URLField(blank=True, default="")
    bio = models.TextField(blank=True, default="")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()


class UserPreferences(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    leaderboard_visible = models.BooleanField(default=True)
    others_learning_visible = models.BooleanField(default=True)
    auto_select_subjects_enabled = models.BooleanField(default=False)
    auto_select_subjects_consent_at = models.DateTimeField(null=True, blank=True)


@receiver(post_save, sender=User)
def create_user_preferences(sender, instance, created, **kwargs):
    if created:
        UserPreferences.objects.create(user=instance)


class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reset_tokens')
    token = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        from django.utils import timezone
        return timezone.now() >= self.expires_at

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets
            self.token = secrets.token_urlsafe(32)
        if not self.expires_at:
            from django.utils import timezone
            from datetime import timedelta
            self.expires_at = timezone.now() + timedelta(hours=1)
        super().save(*args, **kwargs)
