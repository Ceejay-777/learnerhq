from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers as drf_serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .authentication import CookieJWTAuthentication
from .serializers import (
    SignUpSerializer,
    SignInSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    UserProfileSerializer,
    PreferencesUpdateSerializer,
)
from .services import create_user, create_password_reset_token, set_password_with_token

User = get_user_model()


class SuccessDetailSerializer(drf_serializers.Serializer):
    detail = drf_serializers.CharField(help_text="Human-readable result message.")
    status = drf_serializers.CharField(help_text="Always 'success'.")


class UserProfileDataSerializer(drf_serializers.Serializer):
    data = UserProfileSerializer(help_text="User profile data.")
    status = drf_serializers.CharField(help_text="Always 'success'.")


def _set_auth_cookies(response, user):
    refresh = RefreshToken.for_user(user)
    response.set_cookie(
        key='access_token',
        value=str(refresh.access_token),
        httponly=True,
        secure=not settings.DEBUG,
        samesite='Lax',
        max_age=900,
    )
    response.set_cookie(
        key='refresh_token',
        value=str(refresh),
        httponly=True,
        secure=not settings.DEBUG,
        samesite='Strict',
        max_age=604800,
    )


def _clear_auth_cookies(response):
    response.set_cookie('access_token', '', max_age=0)
    response.set_cookie('refresh_token', '', max_age=0)


class SignUpView(GenericAPIView):
    serializer_class = SignUpSerializer

    @extend_schema(
        tags=['Auth'],
        summary='Create account',
        request=SignUpSerializer,
        responses={201: UserProfileDataSerializer},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = dict(serializer.validated_data)
        auto_select = validated.pop('auto_select_subjects_enabled', False)
        user = create_user(**validated)
        if auto_select:
            from django.utils import timezone
            from apps.core.models import UserPreferences
            UserPreferences.objects.filter(user=user).update(
                auto_select_subjects_enabled=True,
                auto_select_subjects_consent_at=timezone.now(),
            )
        response = Response(
            {"data": UserProfileSerializer(user).data, "status": "success"},
            status=status.HTTP_201_CREATED,
        )
        _set_auth_cookies(response, user)
        return response


class SignInView(GenericAPIView):
    serializer_class = SignInSerializer

    @extend_schema(
        tags=['Auth'],
        summary='Sign in',
        request=SignInSerializer,
        responses={200: UserProfileDataSerializer, 401: OpenApiResponse(description='Invalid credentials')},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request=request,
            email=serializer.validated_data['email'],
            password=serializer.validated_data['password'],
        )
        if user is None:
            return Response(
                {"detail": "Invalid email or password.", "status": "error"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        response = Response(
            {"data": UserProfileSerializer(user).data, "status": "success"},
        )
        _set_auth_cookies(response, user)
        return response


class RefreshTokenView(GenericAPIView):

    @extend_schema(
        tags=['Auth'],
        summary='Refresh tokens',
        request=None,
        responses={200: SuccessDetailSerializer, 401: OpenApiResponse(description='Invalid or expired refresh token')},
    )
    def post(self, request):
        refresh_token_value = request.COOKIES.get('refresh_token')
        if not refresh_token_value:
            return Response(
                {"detail": "Refresh token not provided.", "status": "error"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            refresh = RefreshToken(refresh_token_value)
            user = User.objects.get(id=refresh.payload.get('user_id'))
            response = Response({"detail": "Token refreshed.", "status": "success"})
            _set_auth_cookies(response, user)
            return response
        except Exception:
            return Response(
                {"detail": "Invalid or expired refresh token.", "status": "error"},
                status=status.HTTP_401_UNAUTHORIZED,
            )


class SignOutView(GenericAPIView):
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Auth'],
        summary='Sign out',
        request=None,
        responses={200: SuccessDetailSerializer},
    )
    def post(self, request):
        response = Response({"detail": "Signed out.", "status": "success"})
        _clear_auth_cookies(response)
        return response


class PasswordResetRequestView(GenericAPIView):
    serializer_class = PasswordResetRequestSerializer

    @extend_schema(
        tags=['Auth'],
        summary='Request password reset',
        request=PasswordResetRequestSerializer,
        responses={200: SuccessDetailSerializer},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = User.objects.get(email=serializer.validated_data['email'])
            token = create_password_reset_token(user)
            from config.utils.tasks import send_password_reset_email
            try:
                send_password_reset_email.delay(user.email, token.token)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Failed to dispatch password reset email to %s", user.email)
        except User.DoesNotExist:
            pass
        return Response(
            {"detail": "If that email exists, a reset link has been sent.", "status": "success"},
        )


class PasswordResetConfirmView(GenericAPIView):
    serializer_class = PasswordResetConfirmSerializer

    @extend_schema(
        tags=['Auth'],
        summary='Confirm password reset',
        request=PasswordResetConfirmSerializer,
        responses={200: SuccessDetailSerializer},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['reset_token']
        set_password_with_token(
            email=token.user.email,
            token_str=token.token,
            new_password=serializer.validated_data['password'],
        )
        return Response({"detail": "Password reset successful.", "status": "success"})


class RetrieveUpdateProfileView(GenericAPIView):
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    @extend_schema(
        tags=['Profile'],
        summary='Get profile',
        responses={200: UserProfileDataSerializer},
    )
    def get(self, request):
        return Response({"data": self.get_serializer(request.user).data, "status": "success"})

    @extend_schema(
        tags=['Profile'],
        summary='Update profile',
        request=UserProfileSerializer,
        responses={200: UserProfileDataSerializer},
    )
    def patch(self, request):
        prefs_data = request.data.pop('preferences', None)
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for attr, value in serializer.validated_data.items():
            setattr(request.user, attr, value)
        request.user.save(update_fields=list(serializer.validated_data.keys()))

        if prefs_data:
            prefs_serializer = PreferencesUpdateSerializer(
                request.user.preferences, data=prefs_data, partial=True,
            )
            prefs_serializer.is_valid(raise_exception=True)
            if prefs_serializer.validated_data.get('auto_select_subjects_enabled'):
                from django.utils import timezone
                prefs_serializer.validated_data['auto_select_subjects_consent_at'] = timezone.now()
            for attr, value in prefs_serializer.validated_data.items():
                setattr(request.user.preferences, attr, value)
            request.user.preferences.save(update_fields=list(prefs_serializer.validated_data.keys()))

        return Response({"data": self.get_serializer(request.user).data, "status": "success"})
