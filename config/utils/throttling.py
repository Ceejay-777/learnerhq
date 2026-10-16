import math
import time

from django.core.cache import cache
from rest_framework.throttling import BaseThrottle


class BackoffThrottle(BaseThrottle):
    """Per-identity cooldown that doubles after every allowed request.

    Subclass and set ``scope`` and ``get_ident_value``. The wait after the nth
    allowed request is ``min(base_seconds * 2**(n-1), max_seconds)``; the count
    is forgotten after ``reset_seconds`` without requests. Rejections carry
    ``Retry-After`` via DRF's ``Throttled``.
    """

    scope = ""
    base_seconds = 60
    max_seconds = 3600
    reset_seconds = 86400

    def get_ident_value(self, request):
        """Return the identity to throttle, or a falsy value to skip."""
        raise NotImplementedError

    @classmethod
    def _key(cls, ident):
        return f"throttle:{cls.scope}:{ident}"

    @classmethod
    def clear(cls, ident):
        """Forget an identity's backoff, e.g. once the action it guards succeeded."""
        cache.delete(cls._key(ident))

    def allow_request(self, request, view):
        ident = self.get_ident_value(request)
        if not ident:
            return True
        key = self._key(ident)
        lock = f"{key}:lock"
        # add() is atomic, so the lock makes the read-modify-write below atomic
        # across processes (Redis) and threads; a lost race counts as throttled.
        if not cache.add(lock, 1, timeout=5):
            self._wait = 1
            return False
        try:
            now = time.time()
            count, last = cache.get(key, (0, 0.0))
            delay = min(self.base_seconds * 2 ** (count - 1), self.max_seconds) if count else 0
            remaining = last + delay - now
            if remaining > 0:
                self._wait = math.ceil(remaining)
                return False
            cache.set(key, (count + 1, now), self.reset_seconds)
            return True
        finally:
            cache.delete(lock)

    def wait(self):
        return self._wait
