"""
Daily Top-25 High-Probability Picks archive & scorecard.

Workflow
--------
1. snapshot_today()   – capture the current Top-25 picks for `pick_date` with the
                        entry reference price (latest close at pick time).
2. evaluate_picks()   – after market close (~15:30 IST) fetch the realised intraday
                        OHLC for each pick, compute the % move vs the entry price and
                        flag every pick WIN / LOSS / FLAT.
3. get_report()       – return a day's picks with live/closing OHLC + a summary of how
                        many worked out (win-rate, average move). Optionally refreshes
                        live quotes on demand so an intraday "yesterday's report" shows
                        today's open / high / low / current price.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session

from app.models.models import DailyPick
from app.services.screener import StockScreenerEngine

logger = logging.getLogger(__name__)

# A move smaller than this (in %) is treated as flat rather than a win/loss.
FLAT_THRESHOLD = 0.10


class DailyPicksService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # Snapshot
    # ------------------------------------------------------------------ #
    def snapshot_today(self, limit: int = 25, force: bool = False,
                       pick_date: Optional[datetime.date] = None) -> Dict[str, Any]:
        """Capture the current Top-N picks for `pick_date` (default today, IST)."""
        pick_date = pick_date or self._today_ist()

        existing = (
            self.db.query(DailyPick)
            .filter(DailyPick.pick_date == pick_date)
            .count()
        )
        if existing and not force:
            return {"status": "exists", "pick_date": str(pick_date), "count": existing}

        if existing and force:
            self.db.query(DailyPick).filter(DailyPick.pick_date == pick_date).delete()
            self.db.commit()

        engine = StockScreenerEngine(self.db)
        picks = engine.get_top_picks(limit=limit)
        if not picks:
            return {"status": "no_picks", "pick_date": str(pick_date), "count": 0}

        from app.repositories.stock_repo import StockRepository
        stock_repo = StockRepository(self.db)

        rows: List[DailyPick] = []
        for p in picks:
            stock = stock_repo.get_by_symbol(p["symbol"])
            if not stock:
                continue
            rows.append(DailyPick(
                pick_date=pick_date,
                rank=p["rank"],
                stock_id=stock.id,
                symbol=p["symbol"],
                company_name=p.get("company_name"),
                industry=p.get("industry"),
                entry_price=p["price"],
                confidence=p.get("confidence"),
                ml_score=p.get("ml_score"),
                sentiment=p.get("sentiment"),
                prob_plus_1=p.get("prob_plus_1"),
                prob_plus_2=p.get("prob_plus_2"),
                prob_plus_3=p.get("prob_plus_3"),
                prob_plus_5=p.get("prob_plus_5"),
                rsi=p.get("rsi"),
                supertrend_dir=p.get("supertrend_dir"),
                outcome="PENDING",
            ))

        self.db.bulk_save_objects(rows)
        self.db.commit()
        logger.info("Snapshotted %d Top picks for %s", len(rows), pick_date)
        return {"status": "created", "pick_date": str(pick_date), "count": len(rows)}

    # ------------------------------------------------------------------ #
    # Live quotes
    # ------------------------------------------------------------------ #
    def fetch_live_quotes(self, symbols: List[str]) -> Dict[str, Dict[str, float]]:
        """
        Batch-fetch today's OHLC + current price for the given NSE symbols.
        Returns { SYMBOL: {open, high, low, current} }.
        """
        if not symbols:
            return {}

        tickers = [f"{s}.NS" for s in symbols]
        quotes: Dict[str, Dict[str, float]] = {}
        try:
            df = yf.download(
                tickers, period="1d", interval="1d",
                group_by="ticker", progress=False, threads=True,
            )
        except Exception as e:  # pragma: no cover - network
            logger.warning("Live quote batch download failed: %s", e)
            return {}

        if df is None or df.empty:
            return {}

        for sym, tk in zip(symbols, tickers):
            try:
                if len(tickers) == 1:
                    sub = df
                else:
                    if tk not in df.columns.get_level_values(0):
                        continue
                    sub = df[tk]
                sub = sub.dropna(how="all")
                if sub.empty:
                    continue
                last = sub.iloc[-1]
                o, h, l, c = (
                    float(last["Open"]), float(last["High"]),
                    float(last["Low"]), float(last["Close"]),
                )
                if any(pd.isna(v) for v in (o, h, l, c)):
                    continue
                quotes[sym] = {"open": o, "high": h, "low": l, "current": c}
            except Exception:
                continue

        return quotes

    # ------------------------------------------------------------------ #
    # Evaluate
    # ------------------------------------------------------------------ #
    def evaluate_picks(self, pick_date: Optional[datetime.date] = None,
                       eval_date: Optional[datetime.date] = None) -> Dict[str, Any]:
        """
        Fetch realised OHLC for every pick on `pick_date` and flag WIN/LOSS/FLAT
        based on the move from the entry price to the current/closing price.
        """
        pick_date = pick_date or self._today_ist()
        eval_date = eval_date or self._today_ist()

        picks = (
            self.db.query(DailyPick)
            .filter(DailyPick.pick_date == pick_date)
            .all()
        )
        if not picks:
            return {"status": "no_picks", "pick_date": str(pick_date), "evaluated": 0}

        quotes = self.fetch_live_quotes([p.symbol for p in picks])
        wins = losses = flat = evaluated = 0

        for p in picks:
            q = quotes.get(p.symbol)
            if not q:
                continue
            entry = float(p.entry_price) if p.entry_price else 0.0
            current = q["current"]
            change = ((current - entry) / entry * 100) if entry else 0.0

            p.eval_date = eval_date
            p.eval_open = q["open"]
            p.eval_high = q["high"]
            p.eval_low = q["low"]
            p.eval_price = current
            p.change_pct = round(change, 2)

            if change > FLAT_THRESHOLD:
                p.outcome = "WIN"
                wins += 1
            elif change < -FLAT_THRESHOLD:
                p.outcome = "LOSS"
                losses += 1
            else:
                p.outcome = "FLAT"
                flat += 1
            evaluated += 1

        self.db.commit()
        logger.info(
            "Evaluated %d picks for %s: %d WIN / %d LOSS / %d FLAT",
            evaluated, pick_date, wins, losses, flat,
        )
        return {
            "status": "evaluated",
            "pick_date": str(pick_date),
            "eval_date": str(eval_date),
            "evaluated": evaluated,
            "wins": wins,
            "losses": losses,
            "flat": flat,
        }

    # ------------------------------------------------------------------ #
    # Report
    # ------------------------------------------------------------------ #
    def list_dates(self, limit: int = 30) -> List[str]:
        rows = (
            self.db.query(DailyPick.pick_date)
            .distinct()
            .order_by(DailyPick.pick_date.desc())
            .limit(limit)
            .all()
        )
        return [str(r[0]) for r in rows]

    def get_report(self, pick_date: Optional[datetime.date] = None,
                   refresh: bool = False) -> Dict[str, Any]:
        """
        Return the picks for `pick_date` (default the most recent archived day) with
        their realised OHLC + a scorecard summary. When `refresh` is true, live quotes
        are re-fetched first so an intraday report reflects today's prices.
        """
        if pick_date is None:
            latest = (
                self.db.query(DailyPick.pick_date)
                .order_by(DailyPick.pick_date.desc())
                .first()
            )
            if not latest:
                return {"pick_date": None, "summary": None, "picks": []}
            pick_date = latest[0]

        if refresh:
            self.evaluate_picks(pick_date=pick_date, eval_date=self._today_ist())

        picks = (
            self.db.query(DailyPick)
            .filter(DailyPick.pick_date == pick_date)
            .order_by(DailyPick.rank.asc())
            .all()
        )
        if not picks:
            return {"pick_date": str(pick_date), "summary": None, "picks": []}

        out_picks = [self._serialize(p) for p in picks]

        evaluated = [p for p in picks if p.outcome in ("WIN", "LOSS", "FLAT")]
        wins = sum(1 for p in evaluated if p.outcome == "WIN")
        losses = sum(1 for p in evaluated if p.outcome == "LOSS")
        flat = sum(1 for p in evaluated if p.outcome == "FLAT")
        changes = [float(p.change_pct) for p in evaluated if p.change_pct is not None]
        avg_change = round(sum(changes) / len(changes), 2) if changes else None
        best = max(out_picks, key=lambda x: (x["change_pct"] is not None, x["change_pct"] or -1e9)) if changes else None
        worst = min(out_picks, key=lambda x: (x["change_pct"] is None, x["change_pct"] if x["change_pct"] is not None else 1e9)) if changes else None

        summary = {
            "total": len(picks),
            "evaluated": len(evaluated),
            "wins": wins,
            "losses": losses,
            "flat": flat,
            "pending": len(picks) - len(evaluated),
            "win_rate": round(wins / len(evaluated) * 100, 1) if evaluated else None,
            "avg_change_pct": avg_change,
            "best": {"symbol": best["symbol"], "change_pct": best["change_pct"]} if best and best["change_pct"] is not None else None,
            "worst": {"symbol": worst["symbol"], "change_pct": worst["change_pct"]} if worst and worst["change_pct"] is not None else None,
        }

        return {"pick_date": str(pick_date), "summary": summary, "picks": out_picks}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _today_ist() -> datetime.date:
        return (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).date()

    @staticmethod
    def _f(v) -> Optional[float]:
        return float(v) if v is not None else None

    def _serialize(self, p: DailyPick) -> Dict[str, Any]:
        return {
            "rank": p.rank,
            "symbol": p.symbol,
            "company_name": p.company_name,
            "industry": p.industry,
            "entry_price": self._f(p.entry_price),
            "confidence": self._f(p.confidence),
            "ml_score": self._f(p.ml_score),
            "prob_plus_1": self._f(p.prob_plus_1),
            "rsi": self._f(p.rsi),
            "supertrend_dir": p.supertrend_dir,
            "eval_date": str(p.eval_date) if p.eval_date else None,
            "open": self._f(p.eval_open),
            "high": self._f(p.eval_high),
            "low": self._f(p.eval_low),
            "current": self._f(p.eval_price),
            "change_pct": self._f(p.change_pct),
            "outcome": p.outcome,
        }
