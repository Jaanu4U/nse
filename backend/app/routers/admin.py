"""
Admin router — /api/v1/admin/

Credentials: email=admin@admin  password=admin@123
All endpoints except /login require the admin JWT in the Authorization header.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import shutil
import traceback
from datetime import datetime, timedelta
from typing import Optional

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

# ── Hardcoded admin credentials ───────────────────────────────────────────────
ADMIN_EMAIL    = "admin@admin"
ADMIN_PASSWORD = "admin@123"
_ADMIN_CLAIM   = "nse_admin_v1"
_ALGO          = "HS256"


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _make_admin_token() -> str:
    payload = {
        "sub":   _ADMIN_CLAIM,
        "exp":   datetime.utcnow() + timedelta(hours=12),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=_ALGO)


def _verify_admin_token(token: str) -> bool:
    try:
        data = jwt.decode(token, settings.JWT_SECRET, algorithms=[_ALGO])
        return data.get("sub") == _ADMIN_CLAIM
    except Exception:
        return False


def require_admin(authorization: Optional[str] = None):
    """FastAPI dependency — reads Authorization header manually to avoid coupling."""
    from fastapi import Header
    return authorization


from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_bearer = HTTPBearer(auto_error=False)


def admin_guard(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)):
    if creds is None or not _verify_admin_token(creds.credentials):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin auth required")


# ── Pydantic models ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login")
def admin_login(body: LoginRequest):
    if body.email != ADMIN_EMAIL or body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return {"access_token": _make_admin_token(), "token_type": "bearer"}


@router.get("/services", dependencies=[Depends(admin_guard)])
def get_services(db: Session = Depends(get_db)):
    """Check status of all internal services."""
    results = {}

    # 1. PostgreSQL
    try:
        db.execute(text("SELECT 1"))
        results["database"] = {"status": "up", "detail": "PostgreSQL responding"}
    except Exception as e:
        results["database"] = {"status": "down", "detail": str(e)[:120]}

    # 2. Redis
    try:
        import redis as redislib
        r = redislib.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            socket_connect_timeout=2,
        )
        pong = r.ping()
        results["redis"] = {"status": "up" if pong else "down", "detail": "PONG" if pong else "no response"}
    except Exception as e:
        results["redis"] = {"status": "down", "detail": str(e)[:120]}

    # 3. Delta (Java) service
    try:
        resp = httpx.get("http://delta:8090/actuator/health", timeout=3)
        body = resp.json()
        results["delta"] = {
            "status": "up" if body.get("status") == "UP" else "degraded",
            "detail": body.get("status", "unknown"),
        }
    except Exception as e:
        results["delta"] = {"status": "down", "detail": str(e)[:120]}

    # 4. Kite WebSocket auth
    try:
        resp = httpx.get("http://delta:8090/api/kite/status", timeout=3)
        body = resp.json()
        auth = body.get("authenticated", False)
        instruments = body.get("instruments_loaded", 0)
        results["kite"] = {
            "status": "up" if auth else "warning",
            "detail": f"authenticated={auth}, instruments={instruments}",
        }
    except Exception as e:
        results["kite"] = {"status": "down", "detail": str(e)[:120]}

    # 5. Backend scheduler
    try:
        from app.main import app as _app
        sched = getattr(_app.state, "scheduler", None)
        running = sched is not None and sched.running
        results["scheduler"] = {
            "status": "up" if running else "warning",
            "detail": f"running={running}",
        }
    except Exception as e:
        results["scheduler"] = {"status": "down", "detail": str(e)[:120]}

    return results


@router.get("/db", dependencies=[Depends(admin_guard)])
def get_db_stats(db: Session = Depends(get_db)):
    """Row counts + DB size for all tables."""
    rows = db.execute(text("""
        SELECT
            relname               AS table_name,
            n_live_tup            AS row_count,
            pg_size_pretty(pg_total_relation_size(relid)) AS total_size
        FROM pg_stat_user_tables
        ORDER BY n_live_tup DESC
    """)).fetchall()

    db_size = db.execute(text(
        "SELECT pg_size_pretty(pg_database_size(current_database()))"
    )).scalar()

    return {
        "total_db_size": db_size,
        "tables": [
            {"name": r[0], "rows": r[1], "size": r[2]} for r in rows
        ],
    }


def _human_bytes(value: int | float | None) -> str:
    if value is None:
        return "0 B"
    size = float(value)
    units = ["B", "kB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


def _disk_usage_for(path: str) -> dict:
    usage = shutil.disk_usage(path)
    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    return {
        "path": path,
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "total": _human_bytes(total),
        "used": _human_bytes(used),
        "free": _human_bytes(free),
        "used_percent": round((used / total) * 100, 1) if total else 0,
    }


@router.get("/disk", dependencies=[Depends(admin_guard)])
def get_disk_usage():
    """Docker disk usage breakdown for the admin dashboard."""
    try:
        transport = httpx.HTTPTransport(uds="/var/run/docker.sock")
        with httpx.Client(transport=transport, base_url="http://docker", timeout=5.0) as client:
            resp = client.get("/system/df")
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)[:180])

    def summarize_usage(items, key: str = "Size"):
        total = 0
        reclaimable = 0
        count = len(items or [])
        for item in items or []:
            total += int(item.get(key, 0) or 0)
            usage = item.get("UsageData") or {}
            reclaimable += int(usage.get("Size", 0) or 0) if item.get("InUse") is False else 0
        return {
            "count": count,
            "size_bytes": total,
            "size": _human_bytes(total),
            "reclaimable_bytes": reclaimable,
            "reclaimable": _human_bytes(reclaimable),
        }

    images = []
    for img in data.get("Images", []):
        size = int(img.get("Size", 0) or 0)
        shared = int(img.get("SharedSize", 0) or 0)
        images.append({
            "repo_tags": ", ".join(img.get("RepoTags") or []) or "<none>",
            "containers": img.get("Containers", 0),
            "size_bytes": size,
            "size": _human_bytes(size),
            "shared_bytes": shared,
            "shared_size": _human_bytes(shared),
            "unique_bytes": max(size - shared, 0),
            "unique_size": _human_bytes(max(size - shared, 0)),
            "created": datetime.utcfromtimestamp(img.get("Created", 0)).isoformat() if img.get("Created") else None,
        })
    images.sort(key=lambda x: x["size_bytes"], reverse=True)

    volumes = []
    for vol in data.get("Volumes", []):
        usage = vol.get("UsageData") or {}
        size = int(usage.get("Size", 0) or 0)
        volumes.append({
            "name": vol.get("Name"),
            "driver": vol.get("Driver"),
            "ref_count": int(usage.get("RefCount", 0) or 0),
            "size_bytes": size,
            "size": _human_bytes(size),
            "mountpoint": vol.get("Mountpoint"),
        })
    volumes.sort(key=lambda x: x["size_bytes"], reverse=True)

    cache_items = []
    for c in data.get("BuildCache", []):
        size = int(c.get("Size", 0) or 0)
        cache_items.append({
            "id": c.get("ID"),
            "type": c.get("Type"),
            "description": c.get("Description") or "",
            "in_use": bool(c.get("InUse", False)),
            "shared": bool(c.get("Shared", False)),
            "size_bytes": size,
            "size": _human_bytes(size),
            "usage_count": int(c.get("UsageCount", 0) or 0),
            "created_at": c.get("CreatedAt"),
            "last_used_at": c.get("LastUsedAt"),
        })
    cache_items.sort(key=lambda x: x["size_bytes"], reverse=True)

    return {
        "host_filesystems": [
            _disk_usage_for("/"),
            _disk_usage_for("/var/www/html/nse") if os.path.exists("/var/www/html/nse") else None,
        ],
        "layers_size_bytes": int(data.get("LayersSize", 0) or 0),
        "layers_size": _human_bytes(int(data.get("LayersSize", 0) or 0)),
        "images": summarize_usage(data.get("Images", [])),
        "containers": summarize_usage(data.get("Containers", [])),
        "volumes": summarize_usage(data.get("Volumes", [])),
        "build_cache": summarize_usage(data.get("BuildCache", [])),
        "top_images": images[:10],
        "top_volumes": volumes[:10],
        "top_build_cache": cache_items[:10],
    }


@router.get("/errors", dependencies=[Depends(admin_guard)])
def get_errors(
    level: Optional[str] = Query(None, description="ERROR or WARNING"),
    service: Optional[str] = Query(None),
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    """Return captured error/warning log entries."""
    filters = ["1=1"]
    params: dict = {}
    if level:
        filters.append("level = :level")
        params["level"] = level.upper()
    if service:
        filters.append("service = :service")
        params["service"] = service
    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT id, level, service, logger, message, detail, captured_at
            FROM system_errors
            WHERE {where}
            ORDER BY captured_at DESC
            LIMIT :limit
        """),
        {**params, "limit": limit},
    ).fetchall()

    return [
        {
            "id":          r[0],
            "level":       r[1],
            "service":     r[2],
            "logger":      r[3],
            "message":     r[4],
            "detail":      r[5],
            "captured_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]


@router.get("/errors/download", dependencies=[Depends(admin_guard)])
def download_errors(db: Session = Depends(get_db)):
    """Download all errors as CSV."""
    rows = db.execute(text("""
        SELECT id, level, service, logger, message, detail, captured_at
        FROM system_errors ORDER BY captured_at DESC
    """)).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "level", "service", "logger", "message", "detail", "captured_at"])
    for r in rows:
        writer.writerow(list(r))
    buf.seek(0)

    filename = f"errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/errors", dependencies=[Depends(admin_guard)])
def clear_errors(db: Session = Depends(get_db)):
    result = db.execute(text("DELETE FROM system_errors"))
    db.commit()
    return {"deleted": result.rowcount}


@router.get("/errors/recent")
def get_recent_errors(limit: int = Query(30, le=100), db: Session = Depends(get_db)):
    """
    Public read-only summary of recent errors/warnings — no auth required.
    Used by the main dashboard to surface system alerts inline.
    """
    rows = db.execute(text("""
        SELECT id, level, service, logger, message, captured_at
        FROM system_errors
        ORDER BY captured_at DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()
    return [
        {
            "id":          r[0],
            "level":       r[1],
            "service":     r[2],
            "logger":      r[3],
            "message":     r[4],
            "captured_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


@router.get("/logs/download", dependencies=[Depends(admin_guard)])
def download_logs():
    """Stream the backend application log file."""
    log_path = "/var/log/nse_backend.log"
    if not os.path.exists(log_path):
        log_path = "/var/log/train_delta_models.log"
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="No log file found")

    def iter_file():
        with open(log_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    filename = os.path.basename(log_path)
    return StreamingResponse(
        iter_file(),
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Unified log viewer ────────────────────────────────────────────────────────

SOURCES = ["backend", "delta", "db", "redis", "frontend", "scheduler", "system"]


@router.get("/logs", dependencies=[Depends(admin_guard)])
def get_logs(
    source: Optional[str] = Query(None, description=f"One of: {', '.join(SOURCES)}"),
    level:  Optional[str] = Query(None, description="INFO, WARNING, ERROR"),
    search: Optional[str] = Query(None, description="Substring search"),
    limit:  int            = Query(200, le=2000),
    db:     Session        = Depends(get_db),
):
    """Unified log viewer — all sources, all levels."""
    filters = ["1=1"]
    params: dict = {}
    if source:
        filters.append("source = :source")
        params["source"] = source
    if level:
        filters.append("level = :level")
        params["level"] = level.upper()
    if search:
        filters.append("message ILIKE :search")
        params["search"] = f"%{search}%"
    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT id, source, level, message, captured_at
            FROM system_logs
            WHERE {where}
            ORDER BY captured_at DESC
            LIMIT :limit
        """),
        {**params, "limit": limit},
    ).fetchall()
    return [
        {
            "id":          r[0],
            "source":      r[1],
            "level":       r[2],
            "message":     r[3],
            "captured_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


@router.get("/logs/stats", dependencies=[Depends(admin_guard)])
def get_log_stats(db: Session = Depends(get_db)):
    """Count of log entries by source + level for the last 24h."""
    rows = db.execute(text("""
        SELECT source, level, COUNT(*) AS cnt
        FROM system_logs
        WHERE captured_at >= NOW() - INTERVAL '24 hours'
        GROUP BY source, level
        ORDER BY source, level
    """)).fetchall()
    return [{"source": r[0], "level": r[1], "count": r[2]} for r in rows]


@router.get("/logs/download/csv", dependencies=[Depends(admin_guard)])
def download_logs_csv(
    source: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Download system_logs as CSV (filtered by source if specified)."""
    filters = ["1=1"]
    params: dict = {}
    if source:
        filters.append("source = :source")
        params["source"] = source
    rows = db.execute(
        text(f"SELECT id, source, level, message, captured_at FROM system_logs WHERE {' AND '.join(filters)} ORDER BY captured_at DESC"),
        params,
    ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "source", "level", "message", "captured_at"])
    for r in rows:
        writer.writerow(list(r))
    buf.seek(0)
    filename = f"logs_{source or 'all'}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
