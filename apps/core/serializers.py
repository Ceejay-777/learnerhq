from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework import serializers
from .models import PasswordResetToken, UserPreferences

User = get_user_model()


class SignUpSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="User's email address. Must be unique.")
    password = serializers.CharField(write_only=True, help_text="Strong password (min length, not too common, etc.).")
    display_name = serializers.CharField(max_length=100, required=False, allow_blank=True, help_text="Optional display name shown on leaderboard.")
    avatar = serializers.URLField(required=False, allow_blank=True, help_text="Optional URL to avatar image.")
    bio = serializers.CharField(required=False, allow_blank=True, help_text="Optional short biography.")
    auto_select_subjects_enabled = serializers.BooleanField(default=False, help_text="Opt in to automatic subject selection when idle.")

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except ValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value

    def create(self, validated_data):
        auto_select = validated_data.pop('auto_select_subjects_enabled', False)
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        if auto_select:
            from django.utils import timezone
            UserPreferences.objects.filter(user=user).update(
                auto_select_subjects_enabled=True,
                auto_select_subjects_consent_at=timezone.now(),
            )
        return user


class SignInSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="Registered email address.")
    password = serializers.CharField(help_text="Account password.")


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="Email address to send reset link to.")


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="Email address of the account.")
    token = serializers.CharField(help_text="Reset token received via email.")
    password = serializers.CharField(write_only=True, help_text="New password for the account.")

    def validate_password(self, value):
        try:
            validate_password(value)
        except ValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value

    def validate(self, attrs):
        try:
            user = User.objects.get(email=attrs['email'])
            token = PasswordResetToken.objects.get(user=user, token=attrs['token'])
            if token.is_expired():
                raise serializers.ValidationError("Token has expired.")
            attrs['reset_token'] = token
        except (User.DoesNotExist, PasswordResetToken.DoesNotExist):
            raise serializers.ValidationError("Invalid token or email.")
        return attrs


class UserPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreferences
        fields = [
            'leaderboard_visible',
            'others_learning_visible',
            'auto_select_subjects_enabled',
        ]


class UserProfileSerializer(serializers.ModelSerializer):
    preferences = UserPreferencesSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            'email', 'display_name', 'avatar', 'bio',
            'preferences',
            'date_joined',
        ]
        read_only_fields = ['email', 'date_joined', 'preferences']
        extra_kwargs = {
            'display_name': {'help_text': 'Display name shown on leaderboard and to other learners.'},
            'avatar': {'help_text': 'URL to avatar image.'},
            'bio': {'help_text': 'Short biography or tagline.'},
        }


class PreferencesUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreferences
        fields = [
            'leaderboard_visible',
            'others_learning_visible',
            'auto_select_subjects_enabled',
        ]
        extra_kwargs = {
            'leaderboard_visible': {'help_text': 'Whether to show on leaderboards.'},
            'others_learning_visible': {'help_text': 'Whether to show in "others learning" lists.'},
            'auto_select_subjects_enabled': {'help_text': 'Opt in to automatic subject selection when idle.'},
        }
