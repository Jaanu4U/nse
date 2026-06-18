from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, and_, or_
from app.models.models import Stock, PriceDaily, TechnicalIndicator, Financial, Prediction, News
from typing import List, Dict, Any, Optional
import datetime
import os
import json

# Pre-computed 30-day strategy scorecard artifact written by backtest_p3.py.
STRATEGY_SCORECARD_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "models", "strategy_scorecard_30d.json")

# Liquidity / quality guards for Top Picks: high-probability ML scores are only meaningful
# on stocks that are actually tradeable AND on established, quality companies. Penny /
# circuit-locked / near-zero-volume small-caps gap up on a morning pick then fade into the
# close (live scorecard: only the high-turnover tier had a positive realised open->close),
# so the universe is restricted to the liquid quality tier. Average daily traded value is
# used as a size/quality proxy because per-stock fundamentals are only lazily populated for
# a handful of names, whereas turnover is available for the whole universe.
MIN_PICK_PRICE = 20.0            # rupees: drop sub-₹20 penny stocks
MIN_PICK_TURNOVER = 500_000_000  # ₹50 Cr avg daily traded value (~top-430 liquid quality names)
PICK_LIQUIDITY_WINDOW_DAYS = 30

# Hard exclusions for Top Picks (a bullish next-day setup list). Scorecard back-checks
# show these configurations reliably bleed: a confirmed Supertrend downtrend contradicts
# the bullish thesis (0/3 wins, avg -1.56%), and a parabolic RSI blow-off is an exhausted
# move that mean-reverts hard (RSI>=85: 0/1, -3.65%). Names in the RSI 70-85 band still
# win ~83%, so only the *extreme* reading is cut — moderate strength is left untouched.
PICK_MAX_RSI = 85.0              # drop parabolic / blow-off names (>= this RSI)
PICK_EXCLUDE_SUPERTREND_DOWN = True

# Top-5 P(+3%) selection: blend the model's directional probability with a
# cross-sectional VOLATILITY score. analyze_winners.py showed a +2/+3% next-day
# move is volatility-driven (atr_ratio / range_pct lift 1.7x) while trend/RSI add
# nothing to magnitude — yet pure-P(+3%) ranking ignores volatility. Out-of-sample
# walk-forward (backtest_p3.py, 30d & 60d windows) confirms a 50/50 blend roughly
# doubles per-pick return and lifts the +2% hit-rate vs pure P(+3%), at every weight
# tested. Only the two LIVE-available features are used (atr from indicators,
# range_pct from the latest bar) so live and backtest selection stay identical.
PICK_VOL_BLEND_WEIGHT = 0.50     # weight on volatility percentile vs P(+3%) percentile


def pick_trend_multiplier(
    *, close_gt_ema50: Optional[bool], ema20_gt_ema50: Optional[bool],
    close_gt_ema200: Optional[bool], supertrend_dir: Optional[int],
    adx: Optional[float], rsi: Optional[float], dist_ema20: Optional[float] = None,
) -> float:
    """
    Trend-confirmation multiplier (clamped 0.45-1.35) used to re-rank ML picks.

    The raw per-stock ML probabilities (P(next-day return >= +1/2/3/5%)) are inflated by
    volatility — a choppy stock clears a +1% bar often, in *both* directions — so ranking
    on raw ML alone surfaces high-variance mean-reverters. This multiplier rewards a clean
    uptrend stack (price > EMA20 > EMA50 > EMA200, bullish supertrend, trending ADX) and
    penalises downtrends / overbought blow-offs so the picks are directional bullish setups.

    Overbought handling is *graded*: a flat RSI>78 penalty was too weak, letting blow-off
    names (e.g. RSI ~90, stretched well above their 20-EMA) still rank #1. The penalty now
    scales with how extreme RSI is, and an extra overextension penalty fires when price is
    stretched far above its 20-EMA — the configuration that mean-reverts hardest.
    """
    trend_mult = 1.0
    if close_gt_ema50 is not None:
        trend_mult += 0.10 if close_gt_ema50 else -0.18
    if ema20_gt_ema50 is not None:
        trend_mult += 0.06 if ema20_gt_ema50 else -0.06
    if close_gt_ema200 is not None:
        trend_mult += 0.10 if close_gt_ema200 else -0.12
    if supertrend_dir == 1:
        trend_mult += 0.08
    elif supertrend_dir == -1:
        trend_mult -= 0.10
    if adx is not None and adx >= 20:
        trend_mult += 0.05
    if rsi is not None:
        if rsi >= 70:
            # Graded overbought penalty: ~0 at RSI 70, scaling linearly to -0.45 at RSI 95
            # (capped). Extreme blow-off readings (RSI ~90) take a large haircut so they
            # can no longer outrank healthy, less-extended uptrends.
            trend_mult -= min(0.45, (rsi - 70.0) / 25.0 * 0.45)
        elif rsi < 40:
            trend_mult -= 0.06   # weak / no momentum
    if dist_ema20 is not None and dist_ema20 > 0.08:
        # Overextension: price stretched far above its 20-EMA mean-reverts. Graded from
        # 0 at +8% above EMA20 to -0.25 at +25%+ above EMA20.
        trend_mult -= min(0.25, (dist_ema20 - 0.08) / 0.17 * 0.25)
    return max(0.45, min(trend_mult, 1.35))


def pick_volatility_damp(atr_ratio: Optional[float]) -> float:
    """
    Volatility damping factor. ATR/price is a clean daily-volatility proxy: names around
    2-3% are unaffected; choppier names (>3%) are progressively discounted so the ML upside
    probability isn't rewarded for being a coin-flip.
    """
    if atr_ratio is None:
        return 1.0
    return 1.0 / (1.0 + 9.0 * max(0.0, atr_ratio - 0.03))


def pick_final_score(
    ml: float, sentiment: float = 0.0, *,
    close_gt_ema50: Optional[bool] = None, ema20_gt_ema50: Optional[bool] = None,
    close_gt_ema200: Optional[bool] = None, supertrend_dir: Optional[int] = None,
    adx: Optional[float] = None, rsi: Optional[float] = None,
    atr_ratio: Optional[float] = None, dist_ema20: Optional[float] = None,
) -> float:
    """Combine the ML composite with trend confirmation, volatility damping and sentiment."""
    trend_mult = pick_trend_multiplier(
        close_gt_ema50=close_gt_ema50, ema20_gt_ema50=ema20_gt_ema50,
        close_gt_ema200=close_gt_ema200, supertrend_dir=supertrend_dir,
        adx=adx, rsi=rsi, dist_ema20=dist_ema20,
    )
    vol_damp = pick_volatility_damp(atr_ratio)
    return ml * trend_mult * vol_damp + 0.05 * sentiment


class StockScreenerEngine:
    def __init__(self, db: Session):
        self.db = db

    def screen_stocks(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Execute screen query joining Stock, PriceDaily (latest), TechnicalIndicator (latest), and Financials.
        """
        # 1. Get latest dates
        latest_price_date = self.db.query(func.max(PriceDaily.timestamp)).scalar()
        latest_ind_date = self.db.query(func.max(TechnicalIndicator.timestamp)).scalar()
        
        if not latest_price_date:
            return []

        # 2. Base query
        _FinancialSub = aliased(Financial)
        query = self.db.query(Stock, PriceDaily, TechnicalIndicator, Financial)\
            .join(PriceDaily, and_(PriceDaily.stock_id == Stock.id, PriceDaily.timestamp == latest_price_date))\
            .outerjoin(TechnicalIndicator, and_(TechnicalIndicator.stock_id == Stock.id, TechnicalIndicator.timestamp == (latest_ind_date or latest_price_date)))\
            .outerjoin(Financial, and_(Financial.stock_id == Stock.id, Financial.fiscal_year == (
                self.db.query(func.max(_FinancialSub.fiscal_year)).filter(_FinancialSub.stock_id == Stock.id).correlate(Stock).scalar_subquery()
            )))

        # 3. Apply Filters
        
        # Technical Filters
        if "rsi_min" in filters and filters["rsi_min"] is not None:
            query = query.filter(TechnicalIndicator.rsi >= filters["rsi_min"])
        if "rsi_max" in filters and filters["rsi_max"] is not None:
            query = query.filter(TechnicalIndicator.rsi <= filters["rsi_max"])
            
        if "price_above_ema20" in filters and filters["price_above_ema20"]:
            query = query.filter(PriceDaily.close > TechnicalIndicator.ema_20)
        if "price_above_ema50" in filters and filters["price_above_ema50"]:
            query = query.filter(PriceDaily.close > TechnicalIndicator.ema_50)
        if "price_above_ema200" in filters and filters["price_above_ema200"]:
            query = query.filter(PriceDaily.close > TechnicalIndicator.ema_200)
            
        if "macd_cross" in filters and filters["macd_cross"] is not None:
            # BULLISH cross: MACD hist is positive and was negative (approximated here by hist > 0)
            if filters["macd_cross"].upper() == "BULLISH":
                query = query.filter(TechnicalIndicator.macd_hist > 0)
            elif filters["macd_cross"].upper() == "BEARISH":
                query = query.filter(TechnicalIndicator.macd_hist < 0)

        # Volume Breakout Filter (Current volume > N * 20-day SMA of Volume)
        if "volume_breakout" in filters and filters["volume_breakout"]:
            # Approximate by checking volume is above a threshold, e.g., 200,000 or custom ratio
            # For a proper ratio, we can compare to 20-day avg, but in query we can screen by raw volume or typical breakout levels
            query = query.filter(PriceDaily.volume >= 500000)

        # Fundamental Filters
        if "pe_min" in filters and filters["pe_min"] is not None:
            query = query.filter(Financial.pe >= filters["pe_min"])
        if "pe_max" in filters and filters["pe_max"] is not None:
            query = query.filter(Financial.pe <= filters["pe_max"])
            
        if "pb_min" in filters and filters["pb_min"] is not None:
            query = query.filter(Financial.pb >= filters["pb_min"])
        if "pb_max" in filters and filters["pb_max"] is not None:
            query = query.filter(Financial.pb <= filters["pb_max"])
            
        if "roe_min" in filters and filters["roe_min"] is not None:
            query = query.filter(Financial.roe >= filters["roe_min"])
        if "roce_min" in filters and filters["roce_min"] is not None:
            query = query.filter(Financial.roce >= filters["roce_min"])
            
        if "industry" in filters and filters["industry"]:
            query = query.filter(Stock.industry.ilike(f"%{filters['industry']}%"))

        # 4. Fetch results
        results = query.all()
        
        output = []
        for stock, price, indicator, financial in results:
            output.append({
                "symbol": stock.symbol,
                "company_name": stock.company_name,
                "industry": stock.industry,
                "price": float(price.close),
                "volume": int(price.volume),
                "change_pct": float((price.close - price.open) / price.open * 100) if price.open > 0 else 0.0,
                "rsi": float(indicator.rsi) if (indicator and indicator.rsi is not None) else None,
                "macd": float(indicator.macd) if (indicator and indicator.macd is not None) else None,
                "ema_20": float(indicator.ema_20) if (indicator and indicator.ema_20 is not None) else None,
                "ema_200": float(indicator.ema_200) if (indicator and indicator.ema_200 is not None) else None,
                "pe": float(financial.pe) if (financial and financial.pe is not None) else None,
                "pb": float(financial.pb) if (financial and financial.pb is not None) else None,
                "roe": float(financial.roe) if (financial and financial.roe is not None) else None,
                "roce": float(financial.roce) if (financial and financial.roce is not None) else None,
            })
            
        return output

    def get_top_picks(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Rank stocks by a composite ML-probability score using the latest predictions.
        Returns the highest-probability bullish setups with price and momentum context.
        """
        # Per-stock latest dates so each stock uses its own most recent data,
        # avoiding empty results when prediction/price dates drift across stocks.
        latest_pred_sq = (
            self.db.query(
                Prediction.stock_id.label("stock_id"),
                func.max(Prediction.timestamp).label("ts"),
            )
            .group_by(Prediction.stock_id)
            .subquery()
        )
        latest_price_sq = (
            self.db.query(
                PriceDaily.stock_id.label("stock_id"),
                func.max(PriceDaily.timestamp).label("ts"),
            )
            .group_by(PriceDaily.stock_id)
            .subquery()
        )
        latest_ind_sq = (
            self.db.query(
                TechnicalIndicator.stock_id.label("stock_id"),
                func.max(TechnicalIndicator.timestamp).label("ts"),
            )
            .group_by(TechnicalIndicator.stock_id)
            .subquery()
        )

        # Average daily traded value (close * volume) over the recent window, used to
        # screen out illiquid names before ranking. A single day's volume is noisy, so
        # we average across ~30 calendar days (~20 trading sessions).
        latest_global_date = self.db.query(func.max(PriceDaily.timestamp)).scalar()
        liquidity_cutoff = (
            latest_global_date - datetime.timedelta(days=PICK_LIQUIDITY_WINDOW_DAYS)
            if latest_global_date else None
        )
        turnover_sq = (
            self.db.query(
                PriceDaily.stock_id.label("stock_id"),
                func.avg(PriceDaily.close * PriceDaily.volume).label("avg_turnover"),
            )
            .filter(PriceDaily.timestamp >= liquidity_cutoff)
            .group_by(PriceDaily.stock_id)
            .subquery()
        ) if liquidity_cutoff is not None else None

        # Composite confidence: now leans on P(open->high >= +2%) (the graded "star" target).
        score = (
            Prediction.prob_plus_1 * 0.15
            + Prediction.prob_plus_2 * 0.40
            + Prediction.prob_plus_3 * 0.30
            + Prediction.prob_plus_5 * 0.15
        ).label("composite_score")

        query = self.db.query(Stock, PriceDaily, Prediction, TechnicalIndicator, score)\
            .join(latest_pred_sq, latest_pred_sq.c.stock_id == Stock.id)\
            .join(Prediction, and_(
                Prediction.stock_id == Stock.id,
                Prediction.timestamp == latest_pred_sq.c.ts,
            ))\
            .join(latest_price_sq, latest_price_sq.c.stock_id == Stock.id)\
            .join(PriceDaily, and_(
                PriceDaily.stock_id == Stock.id,
                PriceDaily.timestamp == latest_price_sq.c.ts,
            ))\
            .outerjoin(latest_ind_sq, latest_ind_sq.c.stock_id == Stock.id)\
            .outerjoin(TechnicalIndicator, and_(
                TechnicalIndicator.stock_id == Stock.id,
                TechnicalIndicator.timestamp == latest_ind_sq.c.ts,
            ))\
            .filter(Stock.is_active == True)\
            .filter(PriceDaily.close >= MIN_PICK_PRICE)

        # Apply the liquidity floor when we have enough price history to compute it.
        if turnover_sq is not None:
            query = query\
                .join(turnover_sq, turnover_sq.c.stock_id == Stock.id)\
                .filter(turnover_sq.c.avg_turnover >= MIN_PICK_TURNOVER)

        rows = query\
            .order_by(score.desc())\
            .limit(max(limit * 8, 150))\
            .all()

        if not rows:
            return []

        # Recent news sentiment (last 14 days) for the shortlisted stocks, averaged.
        shortlist_ids = [stock.id for (stock, *_rest) in rows]
        sentiment_cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=14)
        sentiment_map = {
            sid: float(avg_s)
            for sid, avg_s in self.db.query(
                News.stock_id, func.avg(News.sentiment_score)
            ).filter(
                News.stock_id.in_(shortlist_ids),
                News.published_at >= sentiment_cutoff,
                News.sentiment_score.isnot(None),
            ).group_by(News.stock_id).all()
            if avg_s is not None
        }

        # Blend the ML composite with momentum, trend confirmation and news sentiment
        # so the final ranking favours probable AND well-supported setups.
        #
        # IMPORTANT: the per-stock ML probabilities (P(next-day return >= +1/2/3/5%))
        # are inflated by *volatility* — a choppy stock clears a +1% bar often, in both
        # directions — so ranking on raw ML alone surfaces volatile mean-reverters that
        # are just as likely to gap down. We therefore (a) damp the score by the stock's
        # ATR-based volatility and (b) require genuine uptrend confirmation, so the picks
        # are directional bullish setups rather than high-variance lottery tickets.
        scored = []
        for (stock, price, pred, indicator, composite) in rows:
            ml = float(composite)
            sentiment = sentiment_map.get(stock.id, 0.0)
            close_v = float(price.close)

            close_gt_ema50 = ema20_gt_ema50 = close_gt_ema200 = None
            supertrend_dir = adx_v = rsi_v = atr_ratio = dist_ema20 = None
            if indicator is not None:
                ema20 = float(indicator.ema_20) if indicator.ema_20 is not None else None
                ema50 = float(indicator.ema_50) if indicator.ema_50 is not None else None
                ema200 = float(indicator.ema_200) if indicator.ema_200 is not None else None
                rsi_v = float(indicator.rsi) if indicator.rsi is not None else None
                adx_v = float(indicator.adx) if indicator.adx is not None else None
                supertrend_dir = getattr(indicator, "supertrend_dir", None)
                if ema50 is not None:
                    close_gt_ema50 = close_v > ema50
                if ema20 is not None and ema50 is not None:
                    ema20_gt_ema50 = ema20 > ema50
                if ema200 is not None:
                    close_gt_ema200 = close_v > ema200
                if ema20 is not None and ema20 > 0:
                    dist_ema20 = (close_v - ema20) / ema20
                if getattr(indicator, "atr", None) is not None and close_v > 0:
                    atr_ratio = float(indicator.atr) / close_v

            # Hard exclusions: a bullish pick list must not surface confirmed downtrends
            # or exhausted parabolic blow-offs (both reliably lose on next-day scorecards).
            if PICK_EXCLUDE_SUPERTREND_DOWN and supertrend_dir == -1:
                continue
            if rsi_v is not None and rsi_v >= PICK_MAX_RSI:
                continue

            final_score = pick_final_score(
                ml, sentiment,
                close_gt_ema50=close_gt_ema50, ema20_gt_ema50=ema20_gt_ema50,
                close_gt_ema200=close_gt_ema200, supertrend_dir=supertrend_dir,
                adx=adx_v, rsi=rsi_v, atr_ratio=atr_ratio, dist_ema20=dist_ema20,
            )
            scored.append((final_score, ml, sentiment, stock, price, pred, indicator))

        scored.sort(key=lambda t: t[0], reverse=True)

        output = []
        for rank, (final_score, ml, sentiment, stock, price, pred, indicator) in enumerate(scored[:limit], start=1):
            sentiment_label = "POSITIVE" if sentiment > 0.15 else ("NEGATIVE" if sentiment < -0.15 else "NEUTRAL")
            output.append({
                "rank": rank,
                "symbol": stock.symbol,
                "company_name": stock.company_name,
                "industry": stock.industry,
                "price": float(price.close),
                "change_pct": float((price.close - price.open) / price.open * 100) if price.open else 0.0,
                "confidence": round(float(final_score) * 100, 1),
                "ml_score": round(ml * 100, 1),
                "sentiment": round(sentiment, 2),
                "sentiment_label": sentiment_label,
                "prob_plus_1": round(float(pred.prob_plus_1) * 100, 1),
                "prob_plus_2": round(float(pred.prob_plus_2) * 100, 1),
                "prob_plus_3": round(float(pred.prob_plus_3) * 100, 1),
                "prob_plus_5": round(float(pred.prob_plus_5) * 100, 1),
                "rsi": float(indicator.rsi) if (indicator and indicator.rsi is not None) else None,
                "supertrend_dir": int(indicator.supertrend_dir) if (indicator and getattr(indicator, "supertrend_dir", None) is not None) else None,
            })

        return output

    @staticmethod
    def _volatility_blend_rank(
        candidates: List[Dict[str, Any]], weight: float
    ) -> List[Dict[str, Any]]:
        """Rank candidates by (1-w)*pctile[P(+3%)] + w*pctile[volatility].

        Volatility score = mean cross-sectional percentile of the two live-available
        drivers (atr_ratio, range_pct). Missing features fall back to the median
        percentile (0.5) so a name is never advantaged or penalised by a gap. Returns
        a new list ordered best-first; the input dicts are annotated with ``blend_score``.
        """
        n = len(candidates)
        if n == 0:
            return []

        def _pctile(values: List[Optional[float]]) -> List[float]:
            idx_present = [i for i, v in enumerate(values) if v is not None]
            if not idx_present:
                return [0.5] * len(values)
            order = sorted(idx_present, key=lambda i: values[i])
            rank_of = {idx: r / max(len(order) - 1, 1) for r, idx in enumerate(order)}
            return [rank_of.get(i, 0.5) for i in range(len(values))]

        pct_p3 = _pctile([c["prob_plus_3"] for c in candidates])
        pct_atr = _pctile([c.get("_atr_ratio") for c in candidates])
        pct_rng = _pctile([c.get("_range_pct") for c in candidates])
        for i, c in enumerate(candidates):
            vol_score = (pct_atr[i] + pct_rng[i]) / 2.0
            c["blend_score"] = round((1 - weight) * pct_p3[i] + weight * vol_score, 4)
        return sorted(candidates, key=lambda c: c["blend_score"], reverse=True)

    def get_plus_target_candidates(
        self, p2_min: float = 50.0, p3_min: float = 50.0, list_limit: int = 60,
        live: bool = False,
    ) -> Dict[str, Any]:
        """
        Scan the liquid universe for next-day move candidates by raw model probability.

        Powers the home-page "+2% / +3% possibility" panel. Returns:
          - counts of stocks clearing several P(+2%) thresholds (30/40/50/60%),
          - the list of stocks with P(+2%) >= ``p2_min``,
          - the list of stocks with P(+3%) >= ``p3_min``,
        all after the same liquidity floor and bullish hard-exclusions used by Top Picks
        (drop confirmed Supertrend downtrends and parabolic RSI blow-offs).
        """
        latest_pred_sq = (
            self.db.query(
                Prediction.stock_id.label("stock_id"),
                func.max(Prediction.timestamp).label("ts"),
            ).group_by(Prediction.stock_id).subquery()
        )
        latest_price_sq = (
            self.db.query(
                PriceDaily.stock_id.label("stock_id"),
                func.max(PriceDaily.timestamp).label("ts"),
            ).group_by(PriceDaily.stock_id).subquery()
        )
        latest_ind_sq = (
            self.db.query(
                TechnicalIndicator.stock_id.label("stock_id"),
                func.max(TechnicalIndicator.timestamp).label("ts"),
            ).group_by(TechnicalIndicator.stock_id).subquery()
        )

        latest_global_date = self.db.query(func.max(PriceDaily.timestamp)).scalar()
        liquidity_cutoff = (
            latest_global_date - datetime.timedelta(days=PICK_LIQUIDITY_WINDOW_DAYS)
            if latest_global_date else None
        )
        turnover_sq = (
            self.db.query(
                PriceDaily.stock_id.label("stock_id"),
                func.avg(PriceDaily.close * PriceDaily.volume).label("avg_turnover"),
            )
            .filter(PriceDaily.timestamp >= liquidity_cutoff)
            .group_by(PriceDaily.stock_id)
            .subquery()
        ) if liquidity_cutoff is not None else None

        query = self.db.query(Stock, PriceDaily, Prediction, TechnicalIndicator)\
            .join(latest_pred_sq, latest_pred_sq.c.stock_id == Stock.id)\
            .join(Prediction, and_(
                Prediction.stock_id == Stock.id,
                Prediction.timestamp == latest_pred_sq.c.ts,
            ))\
            .join(latest_price_sq, latest_price_sq.c.stock_id == Stock.id)\
            .join(PriceDaily, and_(
                PriceDaily.stock_id == Stock.id,
                PriceDaily.timestamp == latest_price_sq.c.ts,
            ))\
            .outerjoin(latest_ind_sq, latest_ind_sq.c.stock_id == Stock.id)\
            .outerjoin(TechnicalIndicator, and_(
                TechnicalIndicator.stock_id == Stock.id,
                TechnicalIndicator.timestamp == latest_ind_sq.c.ts,
            ))\
            .filter(Stock.is_active == True)\
            .filter(PriceDaily.close >= MIN_PICK_PRICE)

        if turnover_sq is not None:
            query = query\
                .join(turnover_sq, turnover_sq.c.stock_id == Stock.id)\
                .filter(turnover_sq.c.avg_turnover >= MIN_PICK_TURNOVER)

        rows = query.all()

        universe = 0
        counts = {"p2_30": 0, "p2_40": 0, "p2_50": 0, "p2_60": 0, "p3_40": 0, "p3_50": 0}
        candidates = []
        for (stock, price, pred, indicator) in rows:
            rsi_v = float(indicator.rsi) if (indicator and indicator.rsi is not None) else None
            supertrend_dir = int(indicator.supertrend_dir) if (
                indicator and getattr(indicator, "supertrend_dir", None) is not None) else None

            # Same bullish hard-exclusions as Top Picks.
            if PICK_EXCLUDE_SUPERTREND_DOWN and supertrend_dir == -1:
                continue
            if rsi_v is not None and rsi_v >= PICK_MAX_RSI:
                continue

            p2 = round(float(pred.prob_plus_2) * 100, 1)
            p3 = round(float(pred.prob_plus_3) * 100, 1)
            universe += 1
            if p2 >= 30.0:
                counts["p2_30"] += 1
            if p2 >= 40.0:
                counts["p2_40"] += 1
            if p2 >= 50.0:
                counts["p2_50"] += 1
            if p2 >= 60.0:
                counts["p2_60"] += 1
            if p3 >= 40.0:
                counts["p3_40"] += 1
            if p3 >= 50.0:
                counts["p3_50"] += 1

            candidates.append({
                "symbol": stock.symbol,
                "company_name": stock.company_name,
                "industry": stock.industry,
                "price": float(price.close),
                "change_pct": float((price.close - price.open) / price.open * 100) if price.open else 0.0,
                "prob_plus_1": round(float(pred.prob_plus_1) * 100, 1),
                "prob_plus_2": p2,
                "prob_plus_3": p3,
                "prob_plus_5": round(float(pred.prob_plus_5) * 100, 1),
                "rsi": rsi_v,
                "supertrend_dir": supertrend_dir,
                # volatility features for the Top-5 blend (live-available only)
                "_atr_ratio": (float(indicator.atr) / float(price.close)
                               if (indicator and indicator.atr is not None and price.close) else None),
                "_range_pct": (float(price.high - price.low) / float(price.close)
                               if (price.high is not None and price.low is not None and price.close) else None),
            })

        by_p2 = sorted(candidates, key=lambda c: c["prob_plus_2"], reverse=True)
        by_p3 = sorted(candidates, key=lambda c: c["prob_plus_3"], reverse=True)

        plus2_list = [c for c in by_p2 if c["prob_plus_2"] >= p2_min][:list_limit]
        plus3_list = [c for c in by_p3 if c["prob_plus_3"] >= p3_min][:list_limit]

        # Fallback "strongest available" lists so the panel is never empty even
        # when no stock clears the threshold on a given day.
        plus2_top = by_p2[:10]

        # Top-5 hold-to-close picks: rank by a blend of the model's P(+3%) percentile
        # and a cross-sectional volatility percentile (atr_ratio + range_pct). This is
        # the OOS-validated "better formula" (see PICK_VOL_BLEND_WEIGHT). The percentiles
        # are computed across the full filtered candidate pool, exactly as in backtest_p3.
        plus3_top = self._volatility_blend_rank(candidates, PICK_VOL_BLEND_WEIGHT)[:10]

        # Live overlay: during market hours attach the running market price + % change
        # vs the entry (the prediction-day close) to the Top-5 hold-to-close picks so the
        # card can be compared against the 9 AM+ live tape.
        live_any = False
        if live and plus3_top:
            try:
                from app.utils.live_quotes import fetch_live_ohlcv
                syms = [c["symbol"] for c in plus3_top[:5]]
                quotes = fetch_live_ohlcv(syms, suffix=".NS")
                for c in plus3_top[:5]:
                    q = quotes.get(c["symbol"])
                    if q and q.get("close"):
                        lp = float(q["close"])
                        entry = float(c.get("price") or 0)
                        c["live_price"] = round(lp, 2)
                        c["live_change_pct"] = round((lp - entry) / entry * 100, 2) if entry else None
                        live_any = True
            except Exception:
                live_any = False

        # Drop internal volatility-feature fields so they don't leak into the API.
        for c in candidates:
            c.pop("_atr_ratio", None)
            c.pop("_range_pct", None)

        return {
            "as_of": latest_global_date.isoformat() if latest_global_date else None,
            "universe": universe,
            "p2_min": p2_min,
            "p3_min": p3_min,
            "live": live_any,
            "counts": counts,
            "plus2_candidates": plus2_list,
            "plus3_candidates": plus3_list,
            "plus2_top": plus2_top,
            "plus3_top": plus3_top,
        }

    def _scorecard_from_artifact(self, days: int, top_n: int) -> Optional[Dict[str, Any]]:
        """Serve the pre-computed 30-day backtest scorecard JSON, reshaped to the same
        percentage format as the live reconstruction. Returns None if no artifact."""
        if not os.path.exists(STRATEGY_SCORECARD_JSON):
            return None
        try:
            with open(STRATEGY_SCORECARD_JSON) as f:
                art = json.load(f)
        except Exception:
            return None
        if art.get("top_n") != top_n:
            return None

        def _pct(v):
            return round(v * 100, 2) if v is not None else None

        def _px(v):
            return round(v, 2) if v is not None else None

        out_days = []
        agg_tot = agg_green = agg_hit2 = agg_hit3 = 0
        agg_cc_sum = 0.0
        for d in art.get("days", [])[:days]:
            out_picks = []
            day_tot = day_green = day_hit2 = day_hit3 = 0
            day_cc = []
            for p in d.get("picks", []):
                cc = p.get("cc")
                out_picks.append({
                    "symbol": p["symbol"],
                    "company_name": p.get("company_name", p["symbol"]),
                    "entry": _px(p.get("entry")),
                    "prob_plus_3": round(p.get("prob_plus_3", 0) * 100, 1),
                    "prob_plus_2": round(p.get("prob_plus_2", 0) * 100, 1),
                    "next_open": _px(p.get("next_open")),
                    "next_high": _px(p.get("next_high")),
                    "next_low": _px(p.get("next_low")),
                    "next_close": _px(p.get("next_close")),
                    "cc": _pct(cc),
                    "ch": _pct(p.get("ch")),
                    "oh": _pct(p.get("oh")),
                    "outcome": p.get("outcome", "PENDING"),
                })
                if cc is not None:
                    day_tot += 1
                    if cc > 0:
                        day_green += 1
                    if cc >= 0.02:
                        day_hit2 += 1
                    if cc >= 0.03:
                        day_hit3 += 1
                    day_cc.append(cc * 100)
            summary = None
            if day_tot:
                summary = {
                    "scored": day_tot,
                    "avg_cc": round(sum(day_cc) / day_tot, 2),
                    "green": day_green,
                    "hit2": day_hit2,
                    "hit3": day_hit3,
                }
                agg_tot += day_tot
                agg_green += day_green
                agg_hit2 += day_hit2
                agg_hit3 += day_hit3
                agg_cc_sum += sum(day_cc)
            out_days.append({
                "pick_date": d.get("pick_date"),
                "result_date": d.get("result_date"),
                "pending": d.get("pending", False),
                "picks": out_picks,
                "summary": summary,
            })

        overall = None
        if agg_tot:
            overall = {
                "scored": agg_tot,
                "avg_cc": round(agg_cc_sum / agg_tot, 2),
                "green": agg_green,
                "win_rate": round(agg_green / agg_tot * 100, 1),
                "hit2": agg_hit2,
                "hit2_rate": round(agg_hit2 / agg_tot * 100, 1),
                "hit3": agg_hit3,
                "hit3_rate": round(agg_hit3 / agg_tot * 100, 1),
            }
        return {
            "top_n": top_n,
            "source": "backtest",
            "generated_at": art.get("generated_at"),
            "days": out_days,
            "overall": overall,
        }

    def strategy_scorecard(self, days: int = 10, top_n: int = 5,
                           refresh: bool = False) -> Dict[str, Any]:
        """
        Reconstruct the locked strategy — Top-N by P(+3%), hold to close — for each
        of the last ``days`` prediction dates and report realized results.

        For every historical prediction date we re-rank the liquid universe by the
        stored P(+3%) (after the same liquidity floor and bullish hard-exclusions as
        Top Picks), take the Top-N, set entry = that day's close, and grade against the
        NEXT trading session's close (close-to-close, matching the backtest). The most
        recent date has no next session yet, so it is returned PENDING.

        When ``refresh`` is set, the in-flight day (whose holding session is today) is
        graded against the running market (live close) so the panel updates intraday.
        """
        # Real forward tracking: reconstruct the strategy from the predictions actually
        # stored each day and grade them against realised closes. As live trading days
        # accumulate these become the scorecard; the pre-computed 30-day walk-forward OOS
        # artifact is only used to BACKFILL older context until enough real days exist.
        artifact = self._scorecard_from_artifact(days, top_n)

        today_ist = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).date()

        # Global ordered trading calendar -> map each prediction date to its next session.
        all_dates = [r[0] for r in self.db.query(PriceDaily.timestamp)
                     .distinct().order_by(PriceDaily.timestamp).all()]
        next_of = {all_dates[i]: all_dates[i + 1] for i in range(len(all_dates) - 1)}

        pred_dates = [r[0] for r in self.db.query(Prediction.timestamp)
                      .group_by(Prediction.timestamp)
                      .having(func.count(Prediction.stock_id) >= 100)
                      .order_by(Prediction.timestamp.desc()).limit(days).all()]
        pred_dates.sort(reverse=True)

        result_days = []
        agg_tot = agg_green = agg_hit2 = agg_hit3 = 0
        agg_cc_sum = 0.0
        for pd_date in pred_dates:
            nd = next_of.get(pd_date)  # next trading session (None for the newest date)
            cutoff = pd_date - datetime.timedelta(days=PICK_LIQUIDITY_WINDOW_DAYS)
            turnover_sq = (
                self.db.query(
                    PriceDaily.stock_id.label("stock_id"),
                    func.avg(PriceDaily.close * PriceDaily.volume).label("avg_turnover"),
                )
                .filter(PriceDaily.timestamp >= cutoff, PriceDaily.timestamp <= pd_date)
                .group_by(PriceDaily.stock_id).subquery()
            )
            rows = self.db.query(Stock, Prediction, TechnicalIndicator, PriceDaily)\
                .join(Prediction, and_(Prediction.stock_id == Stock.id,
                                       Prediction.timestamp == pd_date))\
                .join(PriceDaily, and_(PriceDaily.stock_id == Stock.id,
                                       PriceDaily.timestamp == pd_date))\
                .outerjoin(TechnicalIndicator, and_(TechnicalIndicator.stock_id == Stock.id,
                                                    TechnicalIndicator.timestamp == pd_date))\
                .join(turnover_sq, turnover_sq.c.stock_id == Stock.id)\
                .filter(Stock.is_active == True)\
                .filter(PriceDaily.close >= MIN_PICK_PRICE)\
                .filter(turnover_sq.c.avg_turnover >= MIN_PICK_TURNOVER)\
                .all()

            cands = []
            for stock, pred, ind, price in rows:
                rsi_v = float(ind.rsi) if (ind and ind.rsi is not None) else None
                st = int(ind.supertrend_dir) if (
                    ind and getattr(ind, "supertrend_dir", None) is not None) else None
                if PICK_EXCLUDE_SUPERTREND_DOWN and st == -1:
                    continue
                if rsi_v is not None and rsi_v >= PICK_MAX_RSI:
                    continue
                cands.append({
                    "stock_id": stock.id,
                    "symbol": stock.symbol,
                    "company_name": stock.company_name,
                    "entry": float(price.close),
                    "prob_plus_2": round(float(pred.prob_plus_2) * 100, 1),
                    "prob_plus_3": round(float(pred.prob_plus_3) * 100, 1),
                    "_atr_ratio": (float(ind.atr) / float(price.close)
                                   if (ind and ind.atr is not None and price.close) else None),
                    "_range_pct": (float(price.high - price.low) / float(price.close)
                                   if (price.high is not None and price.low is not None and price.close) else None),
                })
            # Rank by the same P(+3%)+volatility blend the live Top-5 card uses so the
            # scorecard tracks exactly what was picked.
            picks = self._volatility_blend_rank(cands, PICK_VOL_BLEND_WEIGHT)[:top_n]

            # In-flight day: its holding session is today and still trading -> grade
            # against live quotes instead of the (provisional/absent) DB close. During
            # the live session today's price bar does not exist yet, so the in-flight day
            # is the newest prediction date that has no realised next session (nd is None)
            # and lies before today.
            live_day = refresh and nd is None and pd_date < today_ist
            live_quotes = {}
            if live_day and picks:
                from app.utils.live_quotes import fetch_live_ohlcv
                live_quotes = fetch_live_ohlcv([p["symbol"] for p in picks], suffix=".NS")

            nd_prices = {}
            if nd is not None and picks:
                ids = [p["stock_id"] for p in picks]
                for r in self.db.query(PriceDaily).filter(
                        PriceDaily.timestamp == nd, PriceDaily.stock_id.in_(ids)).all():
                    nd_prices[r.stock_id] = r

            day_tot = day_green = day_hit2 = day_hit3 = 0
            day_cc = []
            out_picks = []
            for p in picks:
                rec = {
                    "symbol": p["symbol"],
                    "company_name": p["company_name"],
                    "entry": round(p["entry"], 2),
                    "prob_plus_3": p["prob_plus_3"],
                    "prob_plus_2": p["prob_plus_2"],
                    "next_open": None, "next_high": None, "next_low": None,
                    "next_close": None, "cc": None, "ch": None, "oh": None,
                    "outcome": "PENDING",
                }
                ndp = nd_prices.get(p["stock_id"])
                lq = live_quotes.get(p["symbol"]) if live_day else None
                if lq is not None:
                    no, nh, nl, nc = lq["open"], lq["high"], lq["low"], lq["close"]
                elif ndp is not None:
                    no, nh, nl, nc = (float(ndp.open), float(ndp.high),
                                      float(ndp.low), float(ndp.close))
                else:
                    no = None
                if no is not None:
                    cc = (nc - p["entry"]) / p["entry"] * 100
                    ch = (nh - p["entry"]) / p["entry"] * 100
                    oh = (nh - no) / no * 100 if no else None
                    rec.update({
                        "next_open": round(no, 2), "next_high": round(nh, 2),
                        "next_low": round(nl, 2), "next_close": round(nc, 2),
                        "cc": round(cc, 2), "ch": round(ch, 2),
                        "oh": round(oh, 2) if oh is not None else None,
                        "outcome": "WIN" if cc > 0.1 else ("LOSS" if cc < -0.1 else "FLAT"),
                    })
                    day_tot += 1
                    if cc > 0:
                        day_green += 1
                    if cc >= 2.0:
                        day_hit2 += 1
                    if cc >= 3.0:
                        day_hit3 += 1
                    day_cc.append(cc)
                out_picks.append(rec)

            summary = None
            if day_tot:
                summary = {
                    "scored": day_tot,
                    "avg_cc": round(sum(day_cc) / day_tot, 2),
                    "green": day_green,
                    "hit2": day_hit2,
                    "hit3": day_hit3,
                }
                agg_tot += day_tot
                agg_green += day_green
                agg_hit2 += day_hit2
                agg_hit3 += day_hit3
                agg_cc_sum += sum(day_cc)

            result_days.append({
                "pick_date": pd_date.isoformat(),
                "result_date": (today_ist.isoformat() if live_day
                                else (nd.isoformat() if nd else None)),
                "pending": nd is None and not live_day,
                "live": bool(live_day and day_tot),
                "picks": out_picks,
                "summary": summary,
            })

        overall = None
        if agg_tot:
            overall = {
                "scored": agg_tot,
                "avg_cc": round(agg_cc_sum / agg_tot, 2),
                "green": agg_green,
                "win_rate": round(agg_green / agg_tot * 100, 1),
                "hit2": agg_hit2,
                "hit2_rate": round(agg_hit2 / agg_tot * 100, 1),
                "hit3": agg_hit3,
                "hit3_rate": round(agg_hit3 / agg_tot * 100, 1),
            }
        live_result = {"top_n": top_n, "source": "live",
                       "days": result_days, "overall": overall}
        return self._merge_live_and_backtest(live_result, artifact, days, top_n)

    @staticmethod
    def _overall_from_days(days_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Aggregate win-rate / hit-rate stats across already-graded picks in ``days_list``."""
        tot = green = hit2 = hit3 = 0
        cc_sum = 0.0
        for d in days_list:
            for p in d.get("picks", []):
                cc = p.get("cc")
                if cc is None:
                    continue
                tot += 1
                if cc > 0:
                    green += 1
                if cc >= 2.0:
                    hit2 += 1
                if cc >= 3.0:
                    hit3 += 1
                cc_sum += cc
        if not tot:
            return None
        return {
            "scored": tot,
            "avg_cc": round(cc_sum / tot, 2),
            "green": green,
            "win_rate": round(green / tot * 100, 1),
            "hit2": hit2,
            "hit2_rate": round(hit2 / tot * 100, 1),
            "hit3": hit3,
            "hit3_rate": round(hit3 / tot * 100, 1),
        }

    def _merge_live_and_backtest(self, live_result: Dict[str, Any],
                                 artifact: Optional[Dict[str, Any]],
                                 days: int, top_n: int) -> Dict[str, Any]:
        """Combine real forward days (from stored predictions) with the backtest artifact.

        Real forward days always take precedence for any given date; the backtest only
        fills OLDER dates not yet covered by live tracking, up to ``days`` total. Once
        ``days`` real forward sessions exist the artifact drops out entirely.
        """
        live_days = live_result.get("days") or []
        live_dates = {d["pick_date"] for d in live_days}
        merged = list(live_days)
        if artifact:
            for d in artifact.get("days", []):
                if d.get("pick_date") not in live_dates:
                    merged.append(d)
        merged.sort(key=lambda d: d.get("pick_date") or "", reverse=True)
        merged = merged[:days]

        has_backtest = artifact is not None and any(
            d.get("pick_date") not in live_dates for d in artifact.get("days", []))
        # Count graded (non-pending) real forward days to advertise tracking progress.
        live_graded = sum(1 for d in live_days if not d.get("pending")
                          and (d.get("summary") is not None))
        if has_backtest and live_graded:
            source = "live+backtest"
        elif has_backtest:
            source = "backtest"
        else:
            source = "live"
        return {
            "top_n": top_n,
            "source": source,
            "live_days": live_graded,
            "live": any(d.get("live") for d in merged),
            "generated_at": artifact.get("generated_at") if artifact else None,
            "days": merged,
            "overall": self._overall_from_days(merged),
        }

