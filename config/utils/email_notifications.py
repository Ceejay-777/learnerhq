from config.utils.email_service import send_text_email


def send_password_reset_email(email: str, reset_link: str):
    send_text_email(
        subject="LearnerHQ Password Reset",
        body=f"Click the link below to reset your password:\n\n{reset_link}\n\nThis link expires in 1 hour.",
        recipient=email,
    )
