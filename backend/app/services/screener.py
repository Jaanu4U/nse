from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, and_, or_
from app.models.models import Stock, PriceDaily, TechnicalIndicator, Financial, Prediction, News
from typing import List, Dict, Any, Optional
import datetime

# Liquidity guards for Top Picks: high-probability ML scores are only meaningful on
# stocks that are actually tradeable. Penny / circuit-locked / near-zero-volume names
# produce noise, so they are excluded from the ranking.
MIN_PICK_PRICE = 20.0            # rupees: drop sub-₹20 penny stocks
MIN_PICK_TURNOVER = 50_000_000   # ₹5 Cr average daily traded value over ~30 days
PICK_LIQUIDITY_WINDOW_DAYS = 30


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

    def get_top_picks(self, limit: int = 25) -> List[Dict[str, Any]]:
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

        # Composite confidence: weighted toward near-term moves but rewarding larger upside.
        score = (
            Prediction.prob_plus_1 * 0.40
            + Prediction.prob_plus_2 * 0.30
            + Prediction.prob_plus_3 * 0.20
            + Prediction.prob_plus_5 * 0.10
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

