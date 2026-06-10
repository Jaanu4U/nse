"""
Market-structure analytics computed purely from stored prices/indicators:
sector rotation (industry performance ranking), market breadth (advance/decline,
% above key EMAs, average RSI) and per-symbol correlation vs the NIFTY benchmark
and same-sector peers. All read-only, single bulk SQL queries with a short TTL
cache so the dashboard stays responsive.
"""
import time
import logging
import datetime
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository
from app.models.models import Stock

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL_SECONDS = 600  # 10 minutes


def _cache_get(key):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_SECONDS:
        return hit[1]
    return None


def _cache_set(key, value):
    _CACHE[key] = (time.time(), value)


class MarketStructureService:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)

    # --------------------------------------------------------- sector rotation
    def sector_performance(self) -> dict:
        cached = _cache_get("sectors")
        if cached is not None:
            return cached

        sql = text("""
            WITH ranked AS (
                SELECT p.stock_id, p.close, s.industry,
                       ROW_NUMBER() OVER (PARTITION BY p.stock_id ORDER BY p.timestamp DESC) AS rn
                FROM prices_daily p
                JOIN stocks s ON s.id = p.stock_id
                WHERE s.is_active = true AND s.industry IS NOT NULL AND s.industry <> ''
            ),
            piv AS (
                SELECT stock_id, industry,
                    MAX(CASE WHEN rn = 1  THEN close END) AS c0,
                    MAX(CASE WHEN rn = 6  THEN close END) AS c5,
                    MAX(CASE WHEN rn = 21 THEN close END) AS c20,
                    MAX(CASE WHEN rn = 61 THEN close END) AS c60
                FROM ranked
                WHERE rn IN (1, 6, 21, 61)
                GROUP BY stock_id, industry
            )
            SELECT industry,
                   COUNT(*) AS n,
                   AVG(CASE WHEN c5  > 0 THEN (c0 - c5)  / c5  * 100 END) AS r5,
                   AVG(CASE WHEN c20 > 0 THEN (c0 - c20) / c20 * 100 END) AS r20,
                   AVG(CASE WHEN c60 > 0 THEN (c0 - c60) / c60 * 100 END) AS r60
            FROM piv
            WHERE c0 IS NOT NULL
            GROUP BY industry
            HAVING COUNT(*) >= 3
            ORDER BY r20 DESC NULLS LAST
        """)
        rows = self.db.execute(sql).fetchall()
        sectors = [{
            "industry": r.industry,
            "count": int(r.n),
            "return_5d": round(float(r.r5), 2) if r.r5 is not None else None,
            "return_20d": round(float(r.r20), 2) if r.r20 is not None else None,
            "return_60d": round(float(r.r60), 2) if r.r60 is not None else None,
        } for r in rows]

        result = {
            "as_of": datetime.date.today().isoformat(),
            "sectors": sectors,
            "leaders": sectors[:5],
            "laggards": sectors[-5:][::-1] if len(sectors) >= 5 else [],
        }
        _cache_set("sectors", result)
        return result

    # ----------------------------------------------------------- market breadth
    def market_breadth(self) -> dict:
        cached = _cache_get("breadth")
        if cached is not None:
            return cached

        sql = text("""
            WITH ranked AS (
                SELECT stock_id, close,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY timestamp DESC) AS rn
                FROM prices_daily
            ),
            chg AS (
                SELECT a.stock_id, a.close AS c0, b.close AS c1
                FROM (SELECT stock_id, close FROM ranked WHERE rn = 1) a
                JOIN (SELECT stock_id, close FROM ranked WHERE rn = 2) b USING (stock_id)
            ),
            hl AS (
                SELECT stock_id,
                       MAX(CASE WHEN rn = 1 THEN close END) AS last,
                       MAX(CASE WHEN rn <= 252 THEN close END) AS hi,
                       MIN(CASE WHEN rn <= 252 THEN close END) AS lo
                FROM ranked WHERE rn <= 252 GROUP BY stock_id
            ),
            li AS (
                SELECT DISTINCT ON (stock_id) stock_id, ema_50, ema_200, rsi, supertrend_dir
                FROM technical_indicators
                ORDER BY stock_id, timestamp DESC
            )
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN chg.c0 > chg.c1 THEN 1 ELSE 0 END) AS advances,
                SUM(CASE WHEN chg.c0 < chg.c1 THEN 1 ELSE 0 END) AS declines,
                SUM(CASE WHEN chg.c0 = chg.c1 THEN 1 ELSE 0 END) AS unchanged,
                SUM(CASE WHEN li.ema_50  IS NOT NULL AND chg.c0 > li.ema_50  THEN 1 ELSE 0 END) AS above_50,
                SUM(CASE WHEN li.ema_200 IS NOT NULL AND chg.c0 > li.ema_200 THEN 1 ELSE 0 END) AS above_200,
                AVG(li.rsi) AS avg_rsi,
                SUM(CASE WHEN li.supertrend_dir = 1 THEN 1 ELSE 0 END) AS supertrend_up,
                SUM(CASE WHEN hl.hi IS NOT NULL AND chg.c0 >= hl.hi THEN 1 ELSE 0 END) AS new_highs,
                SUM(CASE WHEN hl.lo IS NOT NULL AND chg.c0 <= hl.lo THEN 1 ELSE 0 END) AS new_lows
            FROM stocks s
            JOIN chg ON chg.stock_id = s.id
            LEFT JOIN li ON li.stock_id = s.id
            LEFT JOIN hl ON hl.stock_id = s.id
            WHERE s.is_active = true
        """)
        r = self.db.execute(sql).fetchone()
        total = int(r.total or 0)
        adv = int(r.advances or 0)
        dec = int(r.declines or 0)
        above_50 = int(r.above_50 or 0)
        above_200 = int(r.above_200 or 0)
        avg_rsi = float(r.avg_rsi) if r.avg_rsi is not None else None

        def pct(x):
            return round(x / total * 100, 1) if total else 0.0

        ad_ratio = round(adv / dec, 2) if dec else float(adv)
        if total:
            score = pct(adv) * 0.4 + pct(above_50) * 0.3 + pct(above_200) * 0.3
        else:
            score = 0.0
        if score >= 65:
            sentiment = "RISK_ON"
        elif score >= 45:
            sentiment = "NEUTRAL"
        else:
            sentiment = "RISK_OFF"

        result = {
            "as_of": datetime.date.today().isoformat(),
            "total": total,
            "advances": adv,
            "declines": dec,
            "unchanged": int(r.unchanged or 0),
            "ad_ratio": ad_ratio,
            "pct_advancing": pct(adv),
            "pct_above_ema50": pct(above_50),
            "pct_above_ema200": pct(above_200),
            "pct_supertrend_up": pct(int(r.supertrend_up or 0)),
            "new_highs_52w": int(r.new_highs or 0),
            "new_lows_52w": int(r.new_lows or 0),
            "avg_rsi": round(avg_rsi, 1) if avg_rsi is not None else None,
            "breadth_score": round(score, 1),
            "sentiment": sentiment,
        }
        _cache_set("breadth", result)
        return result

    # --------------------------------------------------------------- correlation
    def correlation(self, symbol: str, days: int = 90, max_peers: int = 8) -> dict:
        symbol = symbol.upper()
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {}

        start = datetime.date.today() - datetime.timedelta(days=int(days * 1.6) + 10)
        end = datetime.date.today()

        def returns(stock_id):
            prices = self.price_repo.get_daily_prices(stock_id, start, end)
            if len(prices) < 20:
                return None
            s = pd.Series({p.timestamp: float(p.close) for p in prices}).sort_index()
            return s.pct_change().dropna().iloc[-days:]

        base = returns(stock.id)
        if base is None:
            return {"symbol": symbol, "available": False}

        correlations = []

        # vs NIFTY benchmark
        idx = self.stock_repo.get_by_symbol("NIFTY50IDX")
        if idx:
            ir = returns(idx.id)
            if ir is not None:
                joined = pd.concat([base, ir], axis=1, join="inner").dropna()
                if len(joined) >= 20:
                    c = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
                    correlations.append({"symbol": "NIFTY50", "name": "NIFTY 50 Index",
                                         "correlation": round(c, 2), "type": "benchmark"})

        # vs same-industry peers
        if stock.industry:
            peers = (
                self.db.query(Stock)
                .filter_by(industry=stock.industry, is_active=True)
                .all()
            )
            peer_corrs = []
            for p in peers:
                if p.id == stock.id:
                    continue
                pr = returns(p.id)
                if pr is None:
                    continue
                joined = pd.concat([base, pr], axis=1, join="inner").dropna()
                if len(joined) < 20:
                    continue
                c = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
                if np.isfinite(c):
                    peer_corrs.append({"symbol": p.symbol, "name": p.company_name,
                                       "correlation": round(c, 2), "type": "peer"})
            peer_corrs.sort(key=lambda x: x["correlation"], reverse=True)
            correlations.extend(peer_corrs[:max_peers])

        return {
            "symbol": symbol,
            "available": True,
            "window_days": days,
            "industry": stock.industry,
            "correlations": correlations,
        }
