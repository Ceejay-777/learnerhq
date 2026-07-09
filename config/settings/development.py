from .base import *

DEBUG = True
ALLOWED_HOSTS = ['*']

CELERY_TASK_ALWAYS_EAGER = False

SERVICE_BASE_URL = os.environ.get("SERVICE_BASE_URL", "http://localhost:8000")
