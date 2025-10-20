import os
import uuid
import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocketDisconnect
from celery.result import AsyncResult
import redis

from ...queue.celery_app import celery_app
from ...core.config import settings
from ...services.irms_processor import process_file


router = APIRouter()


@router.post("/irms/process")
async def process_irms(file: UploadFile = File(...)) -> JSONResponse:
    job_id = str(uuid.uuid4())
    # Cross-platform temp directory for uploaded files
    base_tmp = Path(tempfile.gettempdir()) / "irms"
    base_tmp.mkdir(parents=True, exist_ok=True)
    tmp_path = str(base_tmp / f"{job_id}_{file.filename}")
    with open(tmp_path, "wb") as f:
        f.write(await file.read())
    # If Celery is disabled, process synchronously and return result directly
    if not settings.USE_CELERY:
        try:
            summary = process_file(tmp_path)
            return JSONResponse({"job_id": job_id, "status": "succeeded", "result": {"summary": summary}})
        except ImportError as e:
            return JSONResponse({"error": "dependency_error", "detail": str(e)}, status_code=500)
        except Exception as e:
            return JSONResponse({"error": "processing_failed", "detail": str(e)}, status_code=500)

    # Fast broker health check to avoid long hangs when Redis is down
    broker_url = settings.CELERY_BROKER_URL or settings.REDIS_URL
    try:
        r = redis.from_url(broker_url, socket_connect_timeout=1, socket_timeout=1)
        r.ping()
    except Exception as e:
        return JSONResponse({"error": "broker_unavailable", "detail": str(e)}, status_code=503)

    task = celery_app.send_task("app.tasks.irms.process", args=[job_id, tmp_path])
    return JSONResponse({"job_id": job_id, "task_id": task.id, "status": "queued"})


@router.get("/jobs/{task_id}")
async def job_status(task_id: str) -> JSONResponse:
    res = AsyncResult(task_id, app=celery_app)
    payload: dict[str, str] = {"task_id": task_id, "state": res.state}
    if res.state == "SUCCESS":
        payload["result"] = res.result  # type: ignore
    return JSONResponse(payload)


@router.websocket("/ws/jobs/{task_id}")
async def job_ws(ws: WebSocket, task_id: str):
    await ws.accept()
    try:
        while True:
            res = AsyncResult(task_id, app=celery_app)
            await ws.send_json({"task_id": task_id, "state": res.state})
            if res.state in ("SUCCESS", "FAILURE", "REVOKED"):
                break
            await ws.receive_text()  # simple keep-alive/ping from client
    except WebSocketDisconnect:
        pass
    finally:
        await ws.close()


@router.get("/sse/jobs/{task_id}")
async def job_sse(task_id: str):
    async def event_stream():
        import asyncio

        while True:
            res = AsyncResult(task_id, app=celery_app)
            data = {"task_id": task_id, "state": res.state}
            yield f"data: {data}\n\n"
            if res.state in ("SUCCESS", "FAILURE", "REVOKED"):
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
