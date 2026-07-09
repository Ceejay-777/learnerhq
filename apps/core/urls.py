from django.urls import path
from .views import (
    SignUpView,
    SignInView,
    RefreshTokenView,
    SignOutView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    RetrieveUpdateProfileView,
)

urlpatterns = [
    path('signup', SignUpView.as_view(), name='sign-up'),
    path('signin', SignInView.as_view(), name='sign-in'),
    path('refresh', RefreshTokenView.as_view(), name='refresh-token'),
    path('signout', SignOutView.as_view(), name='sign-out'),
    path('password-reset/request', PasswordResetRequestView.as_view(), name='request-password-reset'),
    path('password-reset/confirm', PasswordResetConfirmView.as_view(), name='confirm-password-reset'),
    path('profile', RetrieveUpdateProfileView.as_view(), name='profile'),
]
