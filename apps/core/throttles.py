from rest_framework.throttling import AnonRateThrottle

from config.utils.throttling import BackoffThrottle


class PasswordResetEmailThrottle(BackoffThrottle):
    """60s, 2m, 4m ... up to 1h between reset requests for one email, whether or not it exists."""

    scope = "password_reset"

    def get_ident_value(self, request):
        return str(request.data.get("email", "")).strip().lower()


class PasswordResetIPThrottle(AnonRateThrottle):
    scope = "password_reset_ip"
    rate = "30/hour"
