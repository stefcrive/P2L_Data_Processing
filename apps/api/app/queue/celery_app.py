from celery import Celery
from ..core.config import settings


broker = settings.CELERY_BROKER_URL or settings.REDIS_URL
backend = settings.CELERY_BACKEND_URL or settings.REDIS_URL

celery_app = Celery(
    "irms_lab",
    broker=broker,
    backend=backend,
)

celery_app.conf.task_routes = {
    "app.tasks.irms.*": {"queue": "irms"},
}

