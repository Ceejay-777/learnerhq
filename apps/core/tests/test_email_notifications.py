import pytest
from django.core import mail


@pytest.mark.django_db
class TestPasswordResetEmail:

    def test_send_password_reset_email(self):
        from config.utils.email_notifications import send_password_reset_email
        send_password_reset_email("user@test.com", "http://test.com/reset?token=abc")
        assert len(mail.outbox) == 1
        assert "Password Reset" in mail.outbox[0].subject
        assert "http://test.com/reset?token=abc" in mail.outbox[0].body
