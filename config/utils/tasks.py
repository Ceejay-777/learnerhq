import sentry_sdk
from celery import shared_task
from django.conf import settings

from config.utils.email_service import EmailSendError


@shared_task(
    bind=True,
    name='config.send_password_reset_email',
    autoretry_for=(EmailSendError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def send_password_reset_email(self, user_email: str, token: str) -> None:
    from config.utils.email_notifications import send_password_reset_email as send_email
    from django.urls import reverse
    reset_link = f"{settings.SERVICE_BASE_URL}{reverse('confirm-password-reset')}?token={token}&email={user_email}"
    try:
        send_email(user_email, reset_link)
    except EmailSendError:
        # Re-raised so autoretry_for retries; only the last attempt is reported.
        if self.request.retries >= self.max_retries:
            sentry_sdk.capture_message("Password reset email failed after retries", level="error")
        raise
