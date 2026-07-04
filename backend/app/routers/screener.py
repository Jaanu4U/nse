from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.screener import StockScreenerEngine
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/screener", tags=["Stock Screener"])

class ScreenerFilterRequest(BaseModel):
    rsi_min: Optional[float] = None
    rsi_max: Optional[float] = None
    price_above_ema20: Optional[bool] = None
    price_above_ema50: Optional[bool] = None
    price_above_ema200: Optional[bool] = None
    macd_cross: Optional[str] = None # 'BULLISH' or 'BEARISH'
    volume_breakout: Optional[bool] = None
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    pb_min: Optional[float] = None
    pb_max: Optional[float] = None
    roe_min: Optional[float] = None
    roce_min: Optional[float] = None
    industry: Optional[str] = None

@router.post("/")
def run_screener(filters: ScreenerFilterRequest, db: Session = Depends(get_db)):
    engine = StockScreenerEngine(db)
    # Convert request model to dictionary, filtering out None values
    filter_dict = {k: v for k, v in filters.model_dump().items() if v is not None}
    return engine.screen_stocks(filter_dict)


@router.get("/top-picks")
def get_top_picks(limit: int = 5, db: Session = Depends(get_db)):
    """Return the top high-probability stocks ranked by composite ML score."""
    engine = StockScreenerEngine(db)
    return engine.get_top_picks(limit=limit)


@router.get("/plus-targets")
def get_plus_targets(
    p2_min: float = 50.0, p3_min: float = 50.0, list_limit: int = 60,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    """
    Next-day +2% / +3% possibility scan over the liquid universe.

    Returns threshold counts plus the lists of stocks with P(+2%) >= p2_min and
    P(+3%) >= p3_min, after the same liquidity floor and bullish exclusions as Top Picks.
    With refresh=true, overlays the live market price + % change vs entry on the Top-5.
    """
    engine = StockScreenerEngine(db)
    return engine.get_plus_target_candidates(
        p2_min=p2_min, p3_min=p3_min, list_limit=list_limit, live=refresh,
    )


@router.get("/strategy-scorecard")
def get_strategy_scorecard(days: int = 10, top_n: int = 5, refresh: bool = False,
                           db: Session = Depends(get_db)):
    """
    Historical scorecard for the locked strategy (Top-N by P(+3%), hold to close).

    Reconstructs the Top-N by stored P(+3%) for each of the last `days` prediction
    dates and grades each pick against the next session's close (close-to-close).
    With `refresh`, the in-flight day (held today) is graded against live quotes.
    """
    engine = StockScreenerEngine(db)
    return engine.strategy_scorecard(days=days, top_n=top_n, refresh=refresh)


@router.get("/strategy-scorecard-stop")
def get_strategy_scorecard_stop(days: int = 10, top_n: int = 5, refresh: bool = False,
                                db: Session = Depends(get_db)):
    """
    Scorecard for the SIBLING strategy: identical Top-N selection as the locked
    strategy, but exited with a gap-aware -6% disaster stop instead of pure hold-to-close.

    Same reconstruction as /strategy-scorecard; each pick's realized return is re-graded
    through the stop (original close-to-close preserved as ``raw_cc`` + a ``stopped`` flag),
    so the two scorecards can be compared side by side. The original card is unaffected.
    """
    from app.services.screener import PICK_DISASTER_STOP_PCT
    engine = StockScreenerEngine(db)
    return engine.strategy_scorecard(days=days, top_n=top_n, refresh=refresh,
                                     stop_pct=PICK_DISASTER_STOP_PCT)


@router.get("/strategy-scorecard-trail")
def get_strategy_scorecard_trail(days: int = 30, top_n: int = 5,
                                 db: Session = Depends(get_db)):
    """
    Scorecard for the SIBLING strategy: identical Top-N selection as the locked
    strategy, but exited with a 2% trailing stop armed after the pick first runs +2%
    (lock gains once in profit, otherwise hold to close).

    The realized trailing return is intraday-path-dependent, so it is served from the
    real 5-minute backtest artifact written by backtest_trail.py (with the hold-to-close
    baseline over the same window for the head-to-head). The original card is unaffected.
    """
    engine = StockScreenerEngine(db)
    return engine.trail_scorecard(days=days, top_n=top_n)


@router.get("/intraday-strategy")
def get_intraday_strategy_today(db: Session = Depends(get_db)):
    """Today's "Strategy 3% · 3:20 PM" Top-5 picks (live price used as the close)."""
    from app.services.intraday_strategy import IntradayStrategyService
    return IntradayStrategyService(db).get_today()


@router.get("/intraday-strategy/scorecard")
def get_intraday_strategy_scorecard(days: int = 30, refresh: bool = False,
                                    db: Session = Depends(get_db)):
    """
    Forward-tracked scorecard for the 3:20 PM strategy: per-day picks with realised
    next-day OHLC, graded by how far the next day's HIGH ran above the 3:20 PM entry.
    With `refresh`, the in-flight day (playing out today) is graded against live quotes.
    """
    from app.services.intraday_strategy import IntradayStrategyService
    return IntradayStrategyService(db).get_scorecard(days=days, refresh=refresh)


@router.post("/intraday-strategy/run")
def run_intraday_strategy(top_n: int = 5, force: bool = True, db: Session = Depends(get_db)):
    """Manually trigger the 3:20 PM intraday strategy run (snapshot today's Top-N)."""
    from app.services.intraday_strategy import IntradayStrategyService
    return IntradayStrategyService(db).run_strategy(top_n=top_n, force=force)


@router.post("/intraday-strategy/evaluate")
def evaluate_intraday_strategy(db: Session = Depends(get_db)):
    """Manually grade pending 3:20 PM picks against the realised next-day high."""
    from app.services.intraday_strategy import IntradayStrategyService
    return IntradayStrategyService(db).evaluate()


@router.get("/breakout-ready")
def get_breakout_ready(limit: int = 30, min_readiness: int = 55, db: Session = Depends(get_db)):
    """Scan the universe for stocks coiled and ready to break out, ranked by readiness."""
    from app.services.breakout_detection import BreakoutDetectionEngine
    engine = BreakoutDetectionEngine(db)
    return engine.scan_breakout_ready(limit=limit, min_readiness=min_readiness)


@router.get("/breakout-catalog")
def get_breakout_catalog():
    """Reference catalog of breakout patterns with advantages and disadvantages."""
    from app.services.breakout_detection import BREAKOUT_CATALOG
    return [
        {"pattern": key, **meta}
        for key, meta in BREAKOUT_CATALOG.items()
    ]


@router.post("/picks/snapshot")
def snapshot_daily_picks(limit: int = 5, force: bool = False, db: Session = Depends(get_db)):
    """Archive today's Top-N high-probability picks for later scoring."""
    from app.services.daily_picks import DailyPicksService
    service = DailyPicksService(db)
    return service.snapshot_today(limit=limit, force=force)


@router.post("/picks/evaluate")
def evaluate_daily_picks(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Fetch realised OHLC and flag WIN/LOSS for an archived picks day (default today)."""
    import datetime
    from app.services.daily_picks import DailyPicksService
    service = DailyPicksService(db)
    pick_date = datetime.datetime.strptime(date, "%Y-%m-%d").date() if date else None
    return service.evaluate_picks(pick_date=pick_date)


@router.get("/picks/dates")
def list_picks_dates(limit: int = 30, db: Session = Depends(get_db)):
    """List the archived pick dates (most recent first)."""
    from app.services.daily_picks import DailyPicksService
    return DailyPicksService(db).list_dates(limit=limit)


@router.get("/picks/report")
def get_picks_report(date: Optional[str] = None, refresh: bool = False, db: Session = Depends(get_db)):
    """
    Scorecard for an archived picks day: the 25 stocks with their entry price plus
    today's live/closing open, high, low and current price, and a win/loss summary.
    Defaults to the most recent archived day. Pass refresh=true to pull live quotes.
    """
    import datetime
    from app.services.daily_picks import DailyPicksService
    service = DailyPicksService(db)
    pick_date = datetime.datetime.strptime(date, "%Y-%m-%d").date() if date else None
    return service.get_report(pick_date=pick_date, refresh=refresh)
