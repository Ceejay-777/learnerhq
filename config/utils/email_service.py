import logging
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_text_email(subject: str, body: str, recipient: str):
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[recipient],
        )
    except Exception as e:
        logger.error("Email send failed to %s: %s", recipient, e)
        raise Exception(f"Failed to send email to {recipient}") from e


def send_html_email(subject: str, template_path: str, recipient: str, context: dict):
    context.setdefault("base_url", settings.SERVICE_BASE_URL)
    html_content = render_to_string(template_path, context)
    plain_content = ""
    try:
        from django.template.loader import get_template
        from django.template.exceptions import TemplateDoesNotExist
        get_template(template_path.replace(".html", "_plain.txt"))
        plain_content = render_to_string(template_path.replace(".html", "_plain.txt"), context)
    except TemplateDoesNotExist:
        pass

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_content,
            to=[recipient],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()
    except Exception as e:
        logger.error("HTML email send failed to %s: %s", recipient, e)
        raise Exception(f"Failed to send email to {recipient}") from e
