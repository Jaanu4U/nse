"""
One-off / re-runnable backfill of the `stocks.industry` column (sector grouping)
from yfinance, so sector-rotation analytics have data. Safe to re-run: it skips
stocks that already have a sector and commits in small batches.

Usage (inside backend container):
    python populate_sectors.py
"""
import time
import logging
import yfinance as yf

from app.database import SessionLocal
from app.models.models import Stock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("populate_sectors")


def main():
    db = SessionLocal()
    try:
        stocks = (
            db.query(Stock)
            .filter(Stock.is_active == True, Stock.symbol != "NIFTY50IDX")
            .all()
        )
        todo = [s for s in stocks if not s.industry]
        log.info(f"{len(todo)} of {len(stocks)} active stocks need a sector")
        done = 0
        for i, s in enumerate(todo, 1):
            try:
                info = yf.Ticker(f"{s.symbol}.NS").info or {}
                sector = info.get("sector")
                if sector:
                    s.industry = sector
                    done += 1
            except Exception as e:
                log.warning(f"{s.symbol}: {e}")
            if i % 25 == 0:
                db.commit()
                log.info(f"progress {i}/{len(todo)} (set {done})")
            time.sleep(0.4)
        db.commit()
        log.info(f"done: set sector on {done} stocks")
    finally:
        db.close()


if __name__ == "__main__":
    main()
