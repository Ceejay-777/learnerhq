from .base import *
import logging
import os
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

SENTRY_DSN = os.environ.get("SENTRY_DSN")
if SENTRY_DSN:
    sentry_logging = LoggingIntegration(sentry_logs_level=logging.INFO)
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), sentry_logging],
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        send_default_pii=True,
        enable_logs=True,
        environment=os.environ.get("ENVIRONMENT", "production"),
    )

DEBUG = False

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '').split(',')

# Security headers
SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True') == 'True'
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
X_FRAME_OPTIONS = 'DENY'

# CORS
CORS_ALLOWED_ORIGINS = os.getenv('CORS_ALLOWED_ORIGINS', '').split(',') if os.getenv('CORS_ALLOWED_ORIGINS') else []

# Static files
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = 'media/'

STORAGES = {
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}
