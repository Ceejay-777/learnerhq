from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .authentication import CookieJWTAuthentication

EVENT_STREAM_PATH_PREFIX = "/api/events"


class CookieJWTUserMiddleware:
    """Set ``request.user`` from the access_token cookie for the SSE endpoint.

    django-eventstream reads Django's ``request.user``, but this project
    authenticates with JWT cookies at the DRF layer only. Scoped to the event
    stream path so no other route changes behaviour.
    """

    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        self.authenticator = CookieJWTAuthentication()

    def __call__(self, request):
        if request.path.startswith(EVENT_STREAM_PATH_PREFIX):
            try:
                result = self.authenticator.authenticate(request)
            except (InvalidToken, TokenError, AuthenticationFailed):
                result = None
            if result is not None:
                request.user = result[0]
        return self.get_response(request)
