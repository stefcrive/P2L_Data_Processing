from app.queue.celery_app import celery_app
from app.tasks import irms  # noqa: F401 - ensure task registration


if __name__ == "__main__":
    celery_app.worker_main()

