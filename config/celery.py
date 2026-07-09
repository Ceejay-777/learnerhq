import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', 'config.settings.production'))

app = Celery('learnerhq')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    from apps.learning.tasks import (
        dispatch_notifications,
        cleanup_stuck_generations,
        auto_select_subjects,
    )
    sender.add_periodic_task(
        15 * 60,
        dispatch_notifications.s(),
        name='dispatch-notifications-every-15-min',
    )
    sender.add_periodic_task(
        60 * 60,
        cleanup_stuck_generations.s(),
        name='cleanup-stuck-generations-every-hour',
    )
    sender.add_periodic_task(
        15 * 60,
        auto_select_subjects.s(),
        name='auto-select-subjects-every-15-min',
    )


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
