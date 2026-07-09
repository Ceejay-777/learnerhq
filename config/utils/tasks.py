from celery import shared_task
from django.conf import settings


@shared_task(name='config.send_password_reset_email')
def send_password_reset_email(user_email: str, token: str) -> None:
    from config.utils.email_notifications import send_password_reset_email as send_email
    from django.urls import reverse
    reset_link = f"{settings.SERVICE_BASE_URL}{reverse('confirm-password-reset')}?token={token}&email={user_email}"
    send_email(user_email, reset_link)
