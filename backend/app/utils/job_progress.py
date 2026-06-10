"""
Lightweight cross-process job progress tracker. The API server, the daily
scheduler job and the detached regen_all.py script all run inside the same
backend container, so they share progress via a small JSON file in /tmp.

Usage:
    from app.utils.job_progress import set_progress, start_job, finish_job, read_progress
    start_job("daily_sync", total=8, message="Starting daily update")
    set_progress(current=3, message="Calculating indicators")
    finish_job(message="Daily update complete")
"""
import json
import os
import tempfile
import datetime
import logging

logger = logging.getLogger(__name__)

PROGRESS_FILE = os.environ.get("JOB_PROGRESS_FILE", "/tmp/job_progress.json")


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _write(data: dict) -> None:
    try:
        # Atomic write so readers never see a half-written file.
        d = os.path.dirname(PROGRESS_FILE) or "/tmp"
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".jobprog_", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, PROGRESS_FILE)
    except Exception as e:
        logger.warning(f"job_progress write failed: {e}")


def start_job(job: str, total: int = 0, message: str = "") -> None:
    _write({
        "job": job,
        "status": "running",
        "current": 0,
        "total": total,
        "message": message,
        "started_at": _now(),
        "updated_at": _now(),
        "completed_at": None,
    })


def set_progress(current: int = None, total: int = None, message: str = None) -> None:
    data = read_progress() or {}
    if not data:
        return
    if current is not None:
        data["current"] = current
    if total is not None:
        data["total"] = total
    if message is not None:
        data["message"] = message
    data["status"] = "running"
    data["updated_at"] = _now()
    _write(data)


def finish_job(message: str = "Completed") -> None:
    data = read_progress() or {}
    data.setdefault("job", "job")
    data["status"] = "completed"
    if data.get("total"):
        data["current"] = data["total"]
    data["message"] = message
    data["updated_at"] = _now()
    data["completed_at"] = _now()
    _write(data)


def fail_job(message: str = "Failed") -> None:
    data = read_progress() or {}
    data.setdefault("job", "job")
    data["status"] = "failed"
    data["message"] = message
    data["updated_at"] = _now()
    data["completed_at"] = _now()
    _write(data)


def read_progress() -> dict:
    try:
        if not os.path.exists(PROGRESS_FILE):
            return {}
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}
