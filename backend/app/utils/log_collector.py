"""
log_collector.py — Collects logs from all services into system_logs table.

Sources:
  backend    — Python app via logging (this module attaches a DB handler)
  scheduler  — APScheduler jobs (routed through Python logging automatically)
  delta      — Java Spring service (via Docker container stdout)
  db         — PostgreSQL (via Docker container stdout)
  redis      — Redis server (via Docker container stdout)
  frontend   — Next.js (via Docker container stdout)
  system     — Resource alerts: high CPU/memory/disk

Docker logs are polled every 30 s from the host socket (read-only mount).
Each container's log lines are parsed for severity and bulk-inserted.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("log_collector")

# ── Regex patterns for extracting level per source ────────────────────────────

# Java Spring Boot: "2026-07-06T11:02:00Z  INFO 1 --- [thread] class : msg"
_JAVA_RE   = re.compile(r'\s+(INFO|WARN|ERROR|DEBUG|TRACE)\s+')
# PostgreSQL:  "2026-07-06 11:02:00 UTC [1] LOG:  ..." or "ERROR: ..." etc.
_PG_RE     = re.compile(r'\b(LOG|INFO|NOTICE|WARNING|ERROR|FATAL|PANIC|DEBUG)\b', re.IGNORECASE)
# Redis:       "1:M 06 Jul 2026 11:00:00.000 * message"  (* = INFO, # = WARNING, - = VERBOSE)
_REDIS_CHAR = {'*': 'INFO', '#': 'WARNING', '-': 'DEBUG'}
# Python/uvicorn: "[INFO]" or "INFO:" or "ERROR:" patterns
_PYTHON_RE = re.compile(r'\b(INFO|WARNING|WARN|ERROR|DEBUG|CRITICAL)\b')
# Next.js
_NEXT_RE   = re.compile(r'\b(error|warn|info)\b', re.IGNORECASE)


def _parse_level(source: str, line: str) -> str:
    line_lower = line.lower()
    if source == 'delta':
        m = _JAVA_RE.search(line)
        if m:
            v = m.group(1)
            return 'WARNING' if v == 'WARN' else v
    elif source == 'db':
        m = _PG_RE.search(line)
        if m:
            v = m.group(1).upper()
            return {'LOG': 'INFO', 'NOTICE': 'INFO', 'FATAL': 'ERROR', 'PANIC': 'ERROR'}.get(v, v)
    elif source == 'redis':
        # First meaningful char after timestamp
        parts = line.split(' ', 4)
        if len(parts) > 3:
            ch = parts[3].strip()
            return _REDIS_CHAR.get(ch, 'INFO')
    elif source == 'frontend':
        if 'error' in line_lower:  return 'ERROR'
        if 'warn'  in line_lower:  return 'WARNING'
        return 'INFO'
    else:
        m = _PYTHON_RE.search(line)
        if m:
            v = m.group(1).upper()
            return 'WARNING' if v == 'WARN' else ('ERROR' if v == 'CRITICAL' else v)
    # Fallback heuristics
    if 'error' in line_lower or 'exception' in line_lower or 'traceback' in line_lower:
        return 'ERROR'
    if 'warn' in line_lower:
        return 'WARNING'
    return 'INFO'


# ── Database writer (background thread + queue) ───────────────────────────────

class _DbWriter:
    def __init__(self):
        self._q: queue.Queue = queue.Queue(maxsize=5000)
        t = threading.Thread(target=self._drain, daemon=True, name='log-db-writer')
        t.start()

    def push(self, source: str, level: str, message: str):
        try:
            self._q.put_nowait((source, level, message[:2000]))
        except queue.Full:
            pass

    def _drain(self):
        conn = None
        buf: list = []
        while True:
            # Batch up to 200 records or flush every 5 s
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    buf.append(self._q.get(timeout=0.5))
                    if len(buf) >= 200:
                        break
                except queue.Empty:
                    break

            if not buf:
                continue

            for _attempt in range(3):
                try:
                    if conn is None or conn.closed:
                        import psycopg2, psycopg2.extras
                        conn = psycopg2.connect(
                            host=os.getenv('DB_HOST', 'db'),
                            port=int(os.getenv('DB_PORT', 5432)),
                            dbname=os.getenv('DB_NAME', 'nse_stock_db'),
                            user=os.getenv('DB_USER', 'postgres'),
                            password=os.getenv('DB_PASS', 'postgrespassword'),
                        )
                    import psycopg2.extras
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(
                            cur,
                            "INSERT INTO system_logs (source, level, message, captured_at) VALUES %s",
                            [(s, l, m, datetime.now(timezone.utc)) for s, l, m in buf],
                        )
                    conn.commit()
                    buf.clear()
                    break
                except Exception as e:
                    try:
                        if conn: conn.close()
                    except Exception:
                        pass
                    conn = None
                    time.sleep(1)

            if buf:  # all 3 attempts failed — drop silently
                buf.clear()


_writer = _DbWriter()


# ── Python logging handler — captures backend + scheduler logs ────────────────

class DBAllLevelsHandler(logging.Handler):
    """Attaches to root logger; saves INFO+ to system_logs."""
    _SKIP_LOGGERS = frozenset({'log_collector', 'uvicorn.access', 'httpx', 'hpack'})

    def __init__(self):
        super().__init__(level=logging.INFO)

    def emit(self, record: logging.LogRecord):
        # Skip noisy internal loggers
        if any(record.name.startswith(s) for s in self._SKIP_LOGGERS):
            return
        level = record.levelname
        if level == 'CRITICAL': level = 'ERROR'
        if level == 'WARN': level = 'WARNING'
        try:
            msg = self.format(record)
            _writer.push('backend', level, msg[:2000])
        except Exception:
            pass


def attach_python_handler():
    h = DBAllLevelsHandler()
    h.setFormatter(logging.Formatter('%(name)s: %(message)s'))
    logging.getLogger().addHandler(h)
    log.info("DB log handler attached — capturing INFO+ from all Python loggers")


# ── Docker log collector ──────────────────────────────────────────────────────

CONTAINERS = {
    'nse_platform_delta':    'delta',
    'b84bf448046d_nse_platform_db': 'db',
    '8d964515098d_nse_platform_redis': 'redis',
    'nse_platform_frontend': 'frontend',
}


def _resolve_container_names():
    """Dynamically resolve actual container names from docker SDK."""
    try:
        import docker as dockersdk
        client = dockersdk.from_env()
        names = {}
        for c in client.containers.list():
            cname = c.name
            for pattern, source in [
                ('delta',    'delta'),
                ('_db',      'db'),
                ('_redis',   'redis'),
                ('frontend', 'frontend'),
            ]:
                if pattern in cname.lower():
                    names[cname] = source
                    break
        return names
    except Exception as e:
        log.warning("Docker container resolution failed: %s", e)
        return CONTAINERS


def _collect_docker_logs(since: dict[str, datetime]) -> dict[str, datetime]:
    """Poll each container for new log lines since last collection."""
    try:
        import docker as dockersdk
        client = dockersdk.from_env()
    except Exception as e:
        log.warning("Docker client unavailable: %s", e)
        return since

    containers = _resolve_container_names()

    for cname, source in containers.items():
        try:
            container = client.containers.get(cname)
            last = since.get(source)
            kwargs = {'timestamps': True, 'tail': 200 if last is None else 'all', 'stream': False}
            if last:
                # since must be a UTC datetime
                kwargs['since'] = last

            raw: bytes = container.logs(**kwargs)
            lines = raw.decode('utf-8', errors='replace').splitlines()
            new_last = last

            for line in lines:
                if not line.strip():
                    continue
                # Docker prepends a timestamp in RFC3339 format: "2026-07-06T11:02:00.123456789Z message"
                ts_end = line.find(' ')
                message = line[ts_end + 1:].strip() if ts_end != -1 else line.strip()
                if not message:
                    continue
                # Try to parse the container timestamp
                try:
                    ts_str = line[:ts_end].rstrip('Z') if ts_end != -1 else ''
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00')) if ts_str else datetime.now(timezone.utc)
                except Exception:
                    ts = datetime.now(timezone.utc)
                if new_last is None or ts > new_last:
                    new_last = ts

                level = _parse_level(source, message)
                # For non-backend sources keep everything; for frontend keep WARN+
                if source == 'frontend' and level == 'INFO':
                    continue
                _writer.push(source, level, message[:2000])

            if new_last:
                since[source] = new_last
        except Exception as e:
            log.debug("Log collection failed for %s: %s", cname, e)

    return since


def _collect_system_stats():
    """Resource alert: emit WARNING when CPU/memory/disk is high."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        if cpu > 85:
            _writer.push('system', 'WARNING', f"High CPU usage: {cpu:.1f}%")
        if mem.percent > 85:
            _writer.push('system', 'WARNING', f"High memory usage: {mem.percent:.1f}% ({mem.used//1024//1024} MB used)")
        if disk.percent > 85:
            _writer.push('system', 'WARNING', f"High disk usage: {disk.percent:.1f}% ({disk.free//1024//1024//1024} GB free)")
    except Exception:
        pass


def _purge_old_logs():
    """Keep only last 7 days in system_logs to control table size."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'db'),
            port=int(os.getenv('DB_PORT', 5432)),
            dbname=os.getenv('DB_NAME', 'nse_stock_db'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASS', 'postgrespassword'),
        )
        with conn.cursor() as cur:
            cur.execute("DELETE FROM system_logs WHERE captured_at < NOW() - INTERVAL '7 days'")
            cur.execute("DELETE FROM system_errors WHERE captured_at < NOW() - INTERVAL '30 days'")
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug("Log purge failed: %s", e)


def start_log_collector():
    """Start the background collection loop. Call once at app startup."""
    attach_python_handler()

    def loop():
        since: dict[str, datetime] = {}
        last_purge = time.monotonic()
        # Give the app 10 s to finish startup before first collection
        time.sleep(10)
        while True:
            try:
                since = _collect_docker_logs(since)
                _collect_system_stats()
                # Purge old logs every 6 hours
                if time.monotonic() - last_purge > 21600:
                    _purge_old_logs()
                    last_purge = time.monotonic()
            except Exception as e:
                log.error("Log collector loop error: %s", e)
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True, name='log-collector')
    t.start()
    log.info("Log collector started (polling Docker + system stats every 30s)")
