from .base import *

DEBUG = True
ALLOWED_HOSTS = ['*']

CELERY_TASK_ALWAYS_EAGER = False

SERVICE_BASE_URL = os.environ.get("SERVICE_BASE_URL", "http://localhost:8000")

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
        environment=os.environ.get("ENVIRONMENT"),
    )
