from django.contrib.auth import get_user_model
from .models import PasswordResetToken

User = get_user_model()


def create_user(email, password, **profile_data):
    user = User.objects.create_user(email=email, password=password, **profile_data)
    return user


def set_password_with_token(email, token_str, new_password):
    user = User.objects.get(email=email)
    token = PasswordResetToken.objects.get(user=user, token=token_str)
    if token.is_expired():
        raise ValueError("Reset token has expired")
    user.set_password(new_password)
    user.save(update_fields=['password'])
    token.delete()


def create_password_reset_token(user):
    return PasswordResetToken.objects.create(user=user)
