import os
import threading
import uuid
from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.pipeline import run_pipeline, ProjectResult
from services.feishu import send_webhook_notification

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

_jobs: dict[str, dict] = {}


class PipelineRequest(BaseModel):
    start_date: date
    end_date: date


@router.post("/run")
def trigger_pipeline(req: PipelineRequest):
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date")

    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {"status": "running"}

    def _run():
        try:
            results: list[ProjectResult] = run_pipeline(req.start_date, req.end_date)
            succeeded = [r for r in results if r.success]
            failed = [r for r in results if not r.success]
            _jobs[job_id] = {
                "status": "done",
                "success": len(failed) == 0,
                "message": "已执行完毕！" if not failed else f"执行完毕，{len(failed)} 个项目失败",
                "total": len(results),
                "succeeded": len(succeeded),
                "failed": len(failed),
                "details": [
                    {
                        "name": r.name,
                        "success": r.success,
                        "rows_written": r.rows_written,
                        "error": r.error,
                    }
                    for r in results
                ],
            }
            webhook = os.getenv("FEISHU_NOTIFY_WEBHOOK", "")
            if webhook:
                send_webhook_notification(
                    webhook_url=webhook,
                    start_date=req.start_date.isoformat(),
                    end_date=req.end_date.isoformat(),
                    results=_jobs[job_id]["details"],
                )
        except Exception as exc:
            _jobs[job_id] = {"status": "error", "message": str(exc)}

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


@router.get("/status/{job_id}")
def get_status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job 不存在")
    return job
