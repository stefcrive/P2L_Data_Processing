from pydantic import BaseModel


class JobStatus(BaseModel):
    task_id: str
    state: str
    result: dict | None = None

