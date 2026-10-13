"""Channel authorization for django-eventstream.

django-eventstream's default channel manager lets anyone read any channel.
Here a request is always mapped to the authenticated user's own channel, and
that is the only channel they may read.
"""

from django_eventstream.channelmanager import DefaultChannelManager

ANONYMOUS_CHANNEL = "user-anonymous"


def user_channel(user_id) -> str:
    return f"user-{user_id}"


class UserChannelManager(DefaultChannelManager):
    def get_channels_for_request(self, request, view_kwargs):
        # Ignore any client-supplied ``?channel=`` value.
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return {user_channel(user.pk)}
        # Unauthenticated: name a channel nobody can read so the request is
        # rejected as forbidden instead of "no channels specified".
        return {ANONYMOUS_CHANNEL}

    def can_read_channel(self, user, channel):
        return user is not None and channel == user_channel(user.pk)

    def is_channel_reliable(self, channel):
        # No event storage is configured, so events are not replayable.
        return False
