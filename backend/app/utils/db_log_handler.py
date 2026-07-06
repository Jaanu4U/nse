"""
db_log_handler.py — Logging handler that persists ERROR and WARNING records to
the system_errors table so they are visible in the admin dashboard.

Uses a background thread + queue to avoid blocking the hot path.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import traceback


class DBLogHandler(logging.Handler):
    """
    Non-blocking log handler: enqueues records; a daemon thread drains them
    into system_errors via a raw psycopg2 connection (avoids SQLAlchemy
    session lifecycle issues inside log callbacks).
    """

    def __init__(self, service: str = "backend"):
        super().__init__(level=logging.WARNING)
        self.service = service
        self._queue: queue.Queue = queue.Queue(maxsize=500)
        self._thread = threading.Thread(target=self._drain, daemon=True, name="db-log-drainer")
        self._thread.start()

    # ── logging.Handler interface ─────────────────────────────────────────────

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            detail = None
            if record.exc_info:
                detail = "".join(traceback.format_exception(*record.exc_info))
            self._queue.put_nowait((
                record.levelname,   # ERROR / WARNING
                self.service,
                record.name,        # logger name
                msg[:2000],
                detail[:4000] if detail else None,
            ))
        except queue.Full:
            pass  # silently drop if queue is saturated — never block app threads

    # ── Background drainer ────────────────────────────────────────────────────

    def _drain(self) -> None:
        import time
        conn = None
        while True:
            try:
                record_tuple = self._queue.get(timeout=2)
            except queue.Empty:
                continue

            # Lazy-open DB connection
            for _attempt in range(3):
                try:
                    if conn is None or conn.closed:
                        import psycopg2
                        conn = psycopg2.connect(
                            host=os.getenv("DB_HOST", "db"),
                            port=int(os.getenv("DB_PORT", 5432)),
                            dbname=os.getenv("DB_NAME", "nse_stock_db"),
                            user=os.getenv("DB_USER", "postgres"),
                            password=os.getenv("DB_PASS", "postgrespassword"),
                        )
                    level, service, logger_name, message, detail = record_tuple
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO system_errors
                              (level, service, logger, message, detail, captured_at)
                            VALUES (%s, %s, %s, %s, %s, NOW())
                            """,
                            (level, service, logger_name, message, detail),
                        )
                    conn.commit()
                    break
                except Exception:
                    try:
                        if conn:
                            conn.close()
                    except Exception:
                        pass
                    conn = None
                    time.sleep(1)
