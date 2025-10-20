from ..queue.celery_app import celery_app
from ..services.irms_processor import process_file


@celery_app.task(name="app.tasks.irms.process")
def process(job_id: str, file_path: str) -> dict:
    summary = process_file(file_path)
    return {"job_id": job_id, "status": "succeeded", "summary": summary}
