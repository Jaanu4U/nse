"""
"Strategy 3% · 3:20 PM" — intraday run of the locked Top-5 by P(+3%) strategy.

Ten minutes before the NSE close (15:20 IST) the live price is treated as the day's
close, the full prediction pipeline is re-run on it (indicators -> ML predictions), and
the Top-N by P(+3%) are snapshotted so they are actionable *before* the 15:30 close —
unlike the official 18:00 run, whose picks can only be bought the next session.

Because no historical intraday data exists, this strategy cannot be back-tested; instead
the picks are stored each day and graded forward. After the next trading session settles,
the realised next-day OHLC is filled in and each pick is graded by how far the next day's
HIGH ran above the 3:20 PM entry price (entry -> next-high %).
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import StrategyPick, PriceDaily, Stock
from app.services.screener import (
    StockScreenerEngine, MIN_PICK_PRICE, MIN_PICK_TURNOVER,
    PICK_LIQUIDITY_WINDOW_DAYS,
)
from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository

logger = logging.getLogger(__name__)

SESSION_TAG = "1520"
# Realised next-day HIGH must reach this gain above entry for a pick to count as a WIN
# (a realistic intraday sell target consistent with the +2% touch-rate in the backtest).
WIN_HIGH_TARGET = 2.0


class IntradayStrategyService:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _today_ist() -> datetime.date:
        return (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).date()

    @staticmethod
    def _f(v) -> Optional[float]:
        return float(v) if v is not None else None

    def _liquid_symbols(self) -> List[str]:
        """The same liquid quality universe used by Top Picks / the official strategy."""
        latest = self.db.query(func.max(PriceDaily.timestamp)).scalar()
        if latest is None:
            return []
        cutoff = latest - datetime.timedelta(days=PICK_LIQUIDITY_WINDOW_DAYS)
        from sqlalchemy import text
        rows = self.db.execute(text("""
            WITH turnover AS (
                SELECT stock_id, AVG(close*volume) AS avg_turnover
                FROM prices_daily WHERE timestamp >= :cutoff GROUP BY stock_id
            ), lastclose AS (
                SELECT DISTINCT ON (stock_id) stock_id, close
                FROM prices_daily ORDER BY stock_id, timestamp DESC
            )
            SELECT s.symbol
            FROM stocks s
            JOIN turnover t ON t.stock_id = s.id
            JOIN lastclose lc ON lc.stock_id = s.id
            WHERE s.is_active = TRUE AND lc.close >= :minprice AND t.avg_turnover >= :minturn
        """), {"cutoff": cutoff, "minprice": MIN_PICK_PRICE, "minturn": MIN_PICK_TURNOVER})
        return [r[0] for r in rows]

    def _fetch_live_ohlcv(self, symbols: List[str]) -> Dict[str, Dict[str, float]]:
        """Batch-fetch today's intraday OHLCV for NSE symbols -> {SYMBOL: {o,h,l,c,v}}."""
        from app.utils.live_quotes import fetch_live_ohlcv
        return fetch_live_ohlcv(symbols, suffix=".NS")

    # ------------------------------------------------------------------ #
    # Run (15:20 IST)
    # ------------------------------------------------------------------ #
    def run_strategy(self, top_n: int = 5, force: bool = False) -> Dict[str, Any]:
        """
        Treat the 3:20 PM live price as today's close, re-run the prediction pipeline on
        the liquid universe, and snapshot the Top-N by P(+3%) into ``strategy_picks``.
        """
        pick_date = self._today_ist()

        existing = (
            self.db.query(StrategyPick)
            .filter(StrategyPick.pick_date == pick_date, StrategyPick.session == SESSION_TAG)
            .count()
        )
        if existing and not force:
            return {"status": "exists", "pick_date": str(pick_date), "count": existing}

        symbols = self._liquid_symbols()
        if not symbols:
            return {"status": "no_universe", "pick_date": str(pick_date), "count": 0}

        quotes = self._fetch_live_ohlcv(symbols)
        if not quotes:
            return {"status": "no_quotes", "pick_date": str(pick_date), "count": 0}

        # 1) Write the live price as TODAY's close for every liquid stock.
        sym_to_stock = {s.symbol: s for s in self.stock_repo.get_active_stocks()
                        if s.symbol in quotes}
        rows = []
        for sym, q in quotes.items():
            st = sym_to_stock.get(sym)
            if not st:
                continue
            rows.append({
                "stock_id": st.id, "timestamp": pick_date,
                "open": round(q["open"], 2), "high": round(q["high"], 2),
                "low": round(q["low"], 2), "close": round(q["close"], 2),
                "volume": q["volume"], "adj_close": round(q["close"], 2),
            })
        self.price_repo.bulk_upsert_daily(rows)

        # 2) Recompute indicators + 3) re-run predictions for the liquid universe only.
        from app.services.technical_analysis import TechnicalAnalysisEngine
        from app.services.prediction import PredictionEngine
        ta = TechnicalAnalysisEngine(self.db)
        pred = PredictionEngine(self.db)
        for sym in quotes.keys():
            try:
                ta.calculate_and_save_indicators(sym)
            except Exception as e:
                logger.warning("Intraday indicator calc failed for %s: %s", sym, e)
            try:
                pred.predict_next_day(sym, model_type="ensemble")
            except Exception as e:
                logger.warning("Intraday prediction failed for %s: %s", sym, e)

        # 4) Rank the liquid universe by P(+3%) and take the Top-N.
        screener = StockScreenerEngine(self.db)
        targets = screener.get_plus_target_candidates()
        plus3 = (targets.get("plus3_top") or [])[:top_n]
        if not plus3:
            return {"status": "no_picks", "pick_date": str(pick_date), "count": 0}

        # 5) Snapshot the picks (replace any existing snapshot for today).
        self.db.query(StrategyPick).filter(
            StrategyPick.pick_date == pick_date,
            StrategyPick.session == SESSION_TAG,
        ).delete()
        self.db.commit()

        saved: List[StrategyPick] = []
        for rank, c in enumerate(plus3, 1):
            st = sym_to_stock.get(c["symbol"]) or self.stock_repo.get_by_symbol(c["symbol"])
            if not st:
                continue
            saved.append(StrategyPick(
                pick_date=pick_date,
                session=SESSION_TAG,
                rank=rank,
                stock_id=st.id,
                symbol=c["symbol"],
                company_name=c.get("company_name"),
                industry=c.get("industry"),
                entry_price=c["price"],
                prob_plus_2=c.get("prob_plus_2"),
                prob_plus_3=c.get("prob_plus_3"),
                rsi=c.get("rsi"),
                supertrend_dir=c.get("supertrend_dir"),
                outcome="PENDING",
            ))
        self.db.bulk_save_objects(saved)
        self.db.commit()
        logger.info("Intraday 3:20 strategy snapshot: %d picks for %s", len(saved), pick_date)
        return {"status": "created", "pick_date": str(pick_date), "count": len(saved)}

    # ------------------------------------------------------------------ #
    # Evaluate (post 1 AM, once the next session has settled)
    # ------------------------------------------------------------------ #
    def evaluate(self) -> Dict[str, Any]:
        """
        Grade every still-PENDING pick whose NEXT trading session's OHLC is now available.
        Outcome is based on the entry -> next-day HIGH move (the strategy's reach target).
        """
        # Ordered global calendar -> next session for any pick date.
        cal = [r[0] for r in self.db.query(PriceDaily.timestamp)
               .distinct().order_by(PriceDaily.timestamp).all()]
        next_of = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}

        pending = (
            self.db.query(StrategyPick)
            .filter(StrategyPick.outcome == "PENDING")
            .all()
        )
        graded = 0
        for p in pending:
            nd = next_of.get(p.pick_date)
            if nd is None:
                continue  # next session hasn't traded yet
            ndp = (
                self.db.query(PriceDaily)
                .filter(PriceDaily.stock_id == p.stock_id, PriceDaily.timestamp == nd)
                .first()
            )
            if ndp is None:
                continue
            entry = float(p.entry_price) if p.entry_price else 0.0
            if entry <= 0:
                continue
            no, nh, nl, nc = (float(ndp.open), float(ndp.high),
                              float(ndp.low), float(ndp.close))
            ch = (nh - entry) / entry * 100.0
            cc = (nc - entry) / entry * 100.0
            p.result_date = nd
            p.next_open = round(no, 2)
            p.next_high = round(nh, 2)
            p.next_low = round(nl, 2)
            p.next_close = round(nc, 2)
            p.ch_pct = round(ch, 2)
            p.cc_pct = round(cc, 2)
            p.outcome = "WIN" if ch >= WIN_HIGH_TARGET else "LOSS"
            graded += 1
        self.db.commit()
        logger.info("Intraday 3:20 strategy evaluation: graded %d picks", graded)
        return {"status": "evaluated", "graded": graded}

    # ------------------------------------------------------------------ #
    # Report
    # ------------------------------------------------------------------ #
    def list_dates(self, limit: int = 30) -> List[str]:
        rows = (
            self.db.query(StrategyPick.pick_date)
            .filter(StrategyPick.session == SESSION_TAG)
            .distinct()
            .order_by(StrategyPick.pick_date.desc())
            .limit(limit)
            .all()
        )
        return [str(r[0]) for r in rows]

    def get_today(self) -> Dict[str, Any]:
        """Today's 3:20 PM picks for the hero card (entry + probabilities only)."""
        latest = (
            self.db.query(StrategyPick.pick_date)
            .filter(StrategyPick.session == SESSION_TAG)
            .order_by(StrategyPick.pick_date.desc())
            .first()
        )
        if not latest:
            return {"pick_date": None, "picks": []}
        picks = (
            self.db.query(StrategyPick)
            .filter(StrategyPick.pick_date == latest[0], StrategyPick.session == SESSION_TAG)
            .order_by(StrategyPick.rank.asc())
            .all()
        )
        return {
            "pick_date": str(latest[0]),
            "picks": [{
                "rank": p.rank,
                "symbol": p.symbol,
                "company_name": p.company_name,
                "entry": self._f(p.entry_price),
                "prob_plus_3": self._f(p.prob_plus_3),
                "prob_plus_2": self._f(p.prob_plus_2),
            } for p in picks],
        }

    def get_scorecard(self, days: int = 30, refresh: bool = False) -> Dict[str, Any]:
        """Per-day 3:20 PM picks with realised next-day OHLC + an overall summary.

        When ``refresh`` is set and the picks whose holding session is *today* are still
        playing out, those rows are re-graded against the running market (live OHLC) so
        the panel updates intraday instead of showing a frozen snapshot.
        """
        dates = (
            self.db.query(StrategyPick.pick_date)
            .filter(StrategyPick.session == SESSION_TAG)
            .distinct()
            .order_by(StrategyPick.pick_date.desc())
            .limit(days)
            .all()
        )
        dates = [d[0] for d in dates]
        if not dates:
            return {"days": [], "overall": None}

        result_days = []
        agg_tot = agg_green = agg_hit2 = agg_hit3 = 0
        agg_ch_sum = 0.0
        for d in dates:
            picks = (
                self.db.query(StrategyPick)
                .filter(StrategyPick.pick_date == d, StrategyPick.session == SESSION_TAG)
                .order_by(StrategyPick.rank.asc())
                .all()
            )
            out_picks = []
            day_tot = day_green = day_hit2 = day_hit3 = 0
            day_ch = []
            for p in picks:
                ch = self._f(p.ch_pct)
                out_picks.append({
                    "rank": p.rank,
                    "symbol": p.symbol,
                    "company_name": p.company_name,
                    "entry": self._f(p.entry_price),
                    "prob_plus_3": self._f(p.prob_plus_3),
                    "prob_plus_2": self._f(p.prob_plus_2),
                    "next_open": self._f(p.next_open),
                    "next_high": self._f(p.next_high),
                    "next_low": self._f(p.next_low),
                    "next_close": self._f(p.next_close),
                    "ch": ch,
                    "cc": self._f(p.cc_pct),
                    "outcome": p.outcome,
                })
                if ch is not None:
                    day_tot += 1
                    if ch > 0:
                        day_green += 1
                    if ch >= 2.0:
                        day_hit2 += 1
                    if ch >= 3.0:
                        day_hit3 += 1
                    day_ch.append(ch)
            result_date = picks[0].result_date if picks and picks[0].result_date else None
            pending = day_tot == 0
            summary = None
            if day_tot:
                summary = {
                    "scored": day_tot,
                    "avg_ch": round(sum(day_ch) / day_tot, 2),
                    "green": day_green,
                    "hit2": day_hit2,
                    "hit3": day_hit3,
                }
                agg_tot += day_tot
                agg_green += day_green
                agg_hit2 += day_hit2
                agg_hit3 += day_hit3
                agg_ch_sum += sum(day_ch)
            result_days.append({
                "pick_date": str(d),
                "result_date": str(result_date) if result_date else None,
                "pending": pending,
                "picks": out_picks,
                "summary": summary,
            })

        # Live-grade the in-flight day (picks whose holding session is today) so the
        # panel tracks the running market like the daily Picks Scorecard.
        live_any = self._live_refresh_inflight(result_days) if refresh else False

        overall = self._overall_from_result_days(result_days)
        return {"session": SESSION_TAG, "live": live_any,
                "days": result_days, "overall": overall}

    @staticmethod
    def _overall_from_result_days(result_days: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Aggregate win-rate / hit-rate stats across all graded picks in ``result_days``."""
        tot = green = hit2 = hit3 = 0
        ch_sum = 0.0
        for day in result_days:
            for p in day.get("picks", []):
                ch = p.get("ch")
                if ch is None:
                    continue
                tot += 1
                if ch > 0:
                    green += 1
                if ch >= 2.0:
                    hit2 += 1
                if ch >= 3.0:
                    hit3 += 1
                ch_sum += ch
        if not tot:
            return None
        return {
            "scored": tot,
            "avg_ch": round(ch_sum / tot, 2),
            "green": green,
            "win_rate": round(green / tot * 100, 1),
            "hit2": hit2,
            "hit2_rate": round(hit2 / tot * 100, 1),
            "hit3": hit3,
            "hit3_rate": round(hit3 / tot * 100, 1),
        }

    def _live_refresh_inflight(self, result_days: List[Dict[str, Any]]) -> bool:
        """Re-grade the in-flight day's picks against live OHLC. Returns True if any
        day was live-refreshed. The in-flight day is the one whose holding session is
        today (entry was set the prior trading session; the next-day high is forming now).

        The 3:20 strategy holds each pick exactly one session forward, so the day playing
        out *today* is the most recent pick day strictly before today. We resolve it from
        the pick dates themselves (not the price calendar) because today's price bar does
        not exist yet during the live session."""
        today = self._today_ist()

        # Candidate in-flight day = the newest pick day strictly before today.
        prior_days = []
        for day in result_days:
            try:
                pd_date = datetime.date.fromisoformat(day["pick_date"])
            except Exception:
                continue
            if pd_date < today:
                prior_days.append((pd_date, day))
        if not prior_days:
            return False
        prior_days.sort(key=lambda t: t[0], reverse=True)
        _, inflight = prior_days[0]

        live_any = False
        for day in [inflight]:
            syms = [p["symbol"] for p in day["picks"]]
            quotes = self._fetch_live_ohlcv(syms)
            if not quotes:
                continue
            graded = False
            for p in day["picks"]:
                q = quotes.get(p["symbol"])
                entry = p.get("entry")
                if not q or not entry:
                    continue
                no, nh, nl, nc = q["open"], q["high"], q["low"], q["close"]
                ch = (nh - entry) / entry * 100.0
                cc = (nc - entry) / entry * 100.0
                p["next_open"] = round(no, 2)
                p["next_high"] = round(nh, 2)
                p["next_low"] = round(nl, 2)
                p["next_close"] = round(nc, 2)
                p["ch"] = round(ch, 2)
                p["cc"] = round(cc, 2)
                # The high only ratchets up, so a +2% touch is a permanent WIN; otherwise
                # it is still PENDING (could yet be hit before the close), never a LOSS yet.
                p["outcome"] = "WIN" if ch >= WIN_HIGH_TARGET else "PENDING"
                graded = True
            if graded:
                day["pending"] = False
                day["live"] = True
                day["result_date"] = str(today)
                day["summary"] = self._day_summary(day["picks"])
                live_any = True
        return live_any

    @staticmethod
    def _day_summary(picks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        tot = green = hit2 = hit3 = 0
        ch_list = []
        for p in picks:
            ch = p.get("ch")
            if ch is None:
                continue
            tot += 1
            if ch > 0:
                green += 1
            if ch >= 2.0:
                hit2 += 1
            if ch >= 3.0:
                hit3 += 1
            ch_list.append(ch)
        if not tot:
            return None
        return {
            "scored": tot,
            "avg_ch": round(sum(ch_list) / tot, 2),
            "green": green,
            "hit2": hit2,
            "hit3": hit3,
        }
