import math
import random
import datetime
import logging
from sqlalchemy.orm import Session
from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Indian 1-year risk-free proxy (matches the rate used in analytics.py).
RISK_FREE = 0.065
TRADING_DAYS = 252


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(S: float, K: float, T: float, r: float, sigma: float) -> Dict[str, Any]:
    """
    European option pricing and full greeks via Black-Scholes-Merton (no dividends).

    Returns call/put price plus delta, gamma, theta (per day), vega (per 1% vol)
    and rho (per 1% rate). Gamma and vega are shared by calls and puts.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {
            "call": {"price": max(0.0, S - K), "delta": 1.0 if S > K else 0.0,
                     "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0},
            "put": {"price": max(0.0, K - S), "delta": -1.0 if S < K else 0.0,
                    "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0},
        }

    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    nd1, nd2 = _norm_cdf(d1), _norm_cdf(d2)
    n_neg_d1, n_neg_d2 = _norm_cdf(-d1), _norm_cdf(-d2)
    pdf_d1 = _norm_pdf(d1)
    disc = math.exp(-r * T)

    call_price = S * nd1 - K * disc * nd2
    put_price = K * disc * n_neg_d2 - S * n_neg_d1

    # Greeks shared between call & put
    gamma = pdf_d1 / (S * sigma * sqrt_t)
    vega = S * pdf_d1 * sqrt_t / 100.0  # per 1% change in IV

    # Theta is per-year; convert to per-calendar-day.
    call_theta = (-(S * pdf_d1 * sigma) / (2 * sqrt_t) - r * K * disc * nd2) / 365.0
    put_theta = (-(S * pdf_d1 * sigma) / (2 * sqrt_t) + r * K * disc * n_neg_d2) / 365.0

    call_rho = K * T * disc * nd2 / 100.0       # per 1% change in rate
    put_rho = -K * T * disc * n_neg_d2 / 100.0

    return {
        "call": {
            "price": call_price, "delta": nd1, "gamma": gamma,
            "theta": call_theta, "vega": vega, "rho": call_rho,
        },
        "put": {
            "price": put_price, "delta": nd1 - 1.0, "gamma": gamma,
            "theta": put_theta, "vega": vega, "rho": put_rho,
        },
    }


class OptionsChainService:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)

    def _historical_volatility(self, stock_id: int, window: int = 30) -> float:
        """
        Annualized close-to-close realized volatility over `window` trading days.
        Used as the base implied volatility (no live option market to back IV out of).
        """
        end = datetime.date.today()
        start = end - datetime.timedelta(days=window * 3 + 30)
        prices = self.price_repo.get_daily_prices(stock_id, start, end)
        closes = [float(p.close) for p in prices][-(window + 1):]
        if len(closes) < 10:
            return 0.25
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) < 5:
            return 0.25
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        ann_vol = math.sqrt(var) * math.sqrt(TRADING_DAYS)
        # Clamp so an illiquid/quiet stock doesn't produce degenerate greeks.
        return float(min(max(ann_vol, 0.08), 1.50))

    @staticmethod
    def _days_to_monthly_expiry(today: datetime.date) -> int:
        """Calendar days to the monthly F&O expiry (last Thursday of the month)."""
        def last_thursday(year: int, month: int) -> datetime.date:
            nxt = datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)
            last_day = nxt - datetime.timedelta(days=1)
            offset = (last_day.weekday() - 3) % 7  # Thursday = 3
            return last_day - datetime.timedelta(days=offset)

        expiry = last_thursday(today.year, today.month)
        if expiry < today:
            ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            expiry = last_thursday(ny, nm)
        return max((expiry - today).days, 1)

    def get_options_chain(self, symbol: str) -> Dict[str, Any]:
        """
        Builds an options chain priced with real Black-Scholes greeks.

        Pricing, IV (historical volatility + a volatility smile) and the full greek
        set (delta, gamma, theta, vega, rho) are computed analytically. Open interest
        and volume remain modelled (no live NSE option feed available).
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {"strikes": [], "pcr": 1.0, "max_pain": 0.0, "underlying_price": 0.0}

        latest_price = self.price_repo.get_latest_daily_price(stock.id)
        if not latest_price:
            return {"strikes": [], "pcr": 1.0, "max_pain": 0.0, "underlying_price": 0.0}

        underlying_price = float(latest_price.close)
        base_iv = self._historical_volatility(stock.id)
        days_to_expiry = self._days_to_monthly_expiry(datetime.date.today())
        T = days_to_expiry / 365.0

        # Strike interval scales with price.
        if underlying_price < 100:
            strike_interval = 2.5
        elif underlying_price < 500:
            strike_interval = 5.0
        elif underlying_price < 1000:
            strike_interval = 10.0
        elif underlying_price < 3000:
            strike_interval = 20.0
        else:
            strike_interval = 50.0

        atm_strike = round(underlying_price / strike_interval) * strike_interval
        strikes = [atm_strike + i * strike_interval for i in range(-7, 8)]

        option_rows = []
        total_call_oi = 0
        total_put_oi = 0

        for strike in strikes:
            dist = strike - underlying_price
            # Volatility smile: IV rises as the strike moves away from ATM (moneyness).
            moneyness = abs(dist) / underlying_price
            iv = base_iv * (1.0 + 0.6 * moneyness)

            greeks = black_scholes(underlying_price, strike, T, RISK_FREE, iv)
            call_ltp = max(0.05, greeks["call"]["price"])
            put_ltp = max(0.05, greeks["put"]["price"])

            # Modelled open interest: concentrated near ATM and at round strikes.
            multiplier = 2.5 if strike % (strike_interval * 2) == 0 else 1.0
            decay = math.exp(-abs(dist) / (underlying_price * 0.05))
            call_oi = int(max(1000, 100000 * decay * multiplier))
            put_oi = int(max(1000, 100000 * decay * multiplier))

            total_call_oi += call_oi
            total_put_oi += put_oi

            option_rows.append({
                "strike": round(strike, 2),
                "call": {
                    "ltp": round(call_ltp, 2),
                    "oi": call_oi,
                    "oi_change": int(call_oi * random.uniform(-0.15, 0.25)),
                    "volume": int(call_oi * random.uniform(1.2, 5.0)),
                    "iv": round(iv * 100, 2),
                    "delta": round(greeks["call"]["delta"], 3),
                    "gamma": round(greeks["call"]["gamma"], 5),
                    "theta": round(greeks["call"]["theta"], 3),
                    "vega": round(greeks["call"]["vega"], 3),
                    "rho": round(greeks["call"]["rho"], 3),
                },
                "put": {
                    "ltp": round(put_ltp, 2),
                    "oi": put_oi,
                    "oi_change": int(put_oi * random.uniform(-0.10, 0.30)),
                    "volume": int(put_oi * random.uniform(1.1, 4.5)),
                    "iv": round(iv * 100, 2),
                    "delta": round(greeks["put"]["delta"], 3),
                    "gamma": round(greeks["put"]["gamma"], 5),
                    "theta": round(greeks["put"]["theta"], 3),
                    "vega": round(greeks["put"]["vega"], 3),
                    "rho": round(greeks["put"]["rho"], 3),
                }
            })

        pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0

        # Max Pain: settlement strike that minimizes total in-the-money option value.
        min_pain_value = float('inf')
        max_pain_strike = atm_strike
        for test_strike in strikes:
            total_pain = 0.0
            for row in option_rows:
                strike_val = row["strike"]
                if test_strike > strike_val:
                    total_pain += (test_strike - strike_val) * row["call"]["oi"]
                if test_strike < strike_val:
                    total_pain += (strike_val - test_strike) * row["put"]["oi"]
            if total_pain < min_pain_value:
                min_pain_value = total_pain
                max_pain_strike = test_strike

        return {
            "underlying_price": round(underlying_price, 2),
            "atm_strike": round(atm_strike, 2),
            "base_iv": round(base_iv * 100, 2),
            "days_to_expiry": days_to_expiry,
            "pcr": round(pcr, 3),
            "max_pain": round(max_pain_strike, 2),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "strikes": option_rows
        }
