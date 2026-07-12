from .base import *
import logging
import os
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

DEBUG = True
ALLOWED_HOSTS = ['*']

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
        environment="development",
    )

CELERY_TASK_ALWAYS_EAGER = False
SERVICE_BASE_URL = os.environ.get("SERVICE_BASE_URL", "http://localhost:8000")

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_THROTTLE_CLASSES': [],
    'DEFAULT_THROTTLE_RATES': {},
}
