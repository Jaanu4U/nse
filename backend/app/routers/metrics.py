from fastapi import APIRouter, Response
from app.utils.metrics import get_prometheus_metrics
from app.utils.job_progress import read_progress

router = APIRouter(prefix="/metrics", tags=["Metrics & Observability"])

@router.get("")
def get_metrics():
    metrics_data = get_prometheus_metrics()
    return Response(content=metrics_data, media_type="text/plain")


@router.get("/job-status")
def get_job_status():
    """
    Live status of background data-update jobs (daily sync / full regeneration).
    Returns status (idle|running|completed|failed), current/total progress and a
    human-readable message so the web UI can show a progress bar.
    """
    data = read_progress()
    if not data:
        return {"status": "idle", "current": 0, "total": 0, "percent": 0, "message": ""}
    total = data.get("total") or 0
    current = data.get("current") or 0
    data["percent"] = round(current / total * 100, 1) if total else (100 if data.get("status") == "completed" else 0)
    return data

