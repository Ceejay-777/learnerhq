import pytest
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
