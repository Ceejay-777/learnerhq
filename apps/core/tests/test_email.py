import pytest
from unittest import mock
from unittest.mock import patch
from django.core import mail


@pytest.mark.django_db
class TestEmailService:

    def test_send_text_email(self):
        from config.utils.email_service import send_text_email
        send_text_email("Test Subject", "Test body", "test@example.com")
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "Test Subject"
        assert mail.outbox[0].body == "Test body"
        assert mail.outbox[0].to == ["test@example.com"]

    def test_send_text_email_raises_on_failure(self):
        from config.utils.email_service import send_text_email
        with patch("config.utils.email_service.send_mail") as mock_send:
            mock_send.side_effect = Exception("SMTP error")
            with pytest.raises(Exception, match="Failed to send email"):
                send_text_email("Subject", "Body", "fail@example.com")

    def test_send_html_email(self):
        from config.utils.email_service import send_html_email
        with patch("config.utils.email_service.render_to_string") as mock_render:
            mock_render.return_value = "<html>Test</html>"
            send_html_email("HTML Subject", "emails/test.html", "test@example.com", {"key": "value"})
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "HTML Subject"


@mock.patch("config.utils.tasks.sentry_sdk.capture_message")
@mock.patch("config.utils.email_notifications.send_text_email")
def test_reset_email_task_reports_only_on_last_attempt(mock_send, mock_sentry):
    from config.utils.email_service import EmailSendError
    from config.utils.tasks import send_password_reset_email

    mock_send.side_effect = EmailSendError("boom")
    with pytest.raises(EmailSendError):
        send_password_reset_email.apply(args=["u@example.com", "tok"], retries=3, throw=True)
    mock_sentry.assert_called_once()
