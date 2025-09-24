import os
import uuid
from fastapi import APIRouter, UploadFile, File, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect
from celery.result import AsyncResult

from ...queue.celery_app import celery_app


router = APIRouter()


@router.post("/irms/process")
async def process_irms(file: UploadFile = File(...)) -> JSONResponse:
    job_id = str(uuid.uuid4())
    os.makedirs("/tmp/irms", exist_ok=True)
    tmp_path = f"/tmp/irms/{job_id}_{file.filename}"
    with open(tmp_path, "wb") as f:
        f.write(await file.read())
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

