import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
try:
    from .celery import app as celery_app

    __all__ = ("celery_app",)
except ImportError:  # Celery is not installed: the run_checks command still works
    celery_app = None