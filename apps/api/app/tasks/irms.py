from time import sleep
from ..queue.celery_app import celery_app


@celery_app.task(name="app.tasks.irms.process")
def process(job_id: str, file_path: str) -> dict:
    # TODO: integrate IRMS_output_analyzer in later step
    sleep(2)
    return {"job_id": job_id, "status": "succeeded", "summary": "Processed stub"}

