import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "poc_project.settings")

app = Celery("system_a")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["system_a_mq"])
