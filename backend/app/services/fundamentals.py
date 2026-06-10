"""
Real fundamentals via yfinance (.info / statements) with a lightweight DCF
intrinsic-value model. Ratios are persisted to the `financials` table so the
screener can filter on them; the richer payload (DCF, market cap, growth,
quarterly trend) is returned live and cached per-symbol with a TTL.
"""
import time
import logging
import datetime
import math
import yfinance as yf
from sqlalchemy.orm import Session

from app.repositories.stock_repo import StockRepository
from app.repositories.financials_repo import FinancialsRepository

logger = logging.getLogger(__name__)

# Per-symbol in-process cache so repeated views don't re-hit yfinance.
_CACHE: dict = {}
_TTL_SECONDS = 6 * 3600  # 6 hours

# DCF assumptions (5-year explicit horizon).
_DCF_DISCOUNT = 0.12          # required return / WACC proxy
_DCF_TERMINAL_GROWTH = 0.04   # perpetual growth after year 5
_DCF_HORIZON = 5


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


class FundamentalsService:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.fin_repo = FinancialsRepository(db)

    # ------------------------------------------------------------------ public
    def get_fundamentals(self, symbol: str, force: bool = False) -> dict:
        symbol = symbol.upper()
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {}

        now = time.time()
        if not force:
            cached = _CACHE.get(symbol)
            if cached and now - cached[0] < _TTL_SECONDS:
                return cached[1]

        payload = self._fetch_yf(symbol)
        if payload is None:
            # yfinance failed — fall back to whatever ratios are stored in the DB.
            latest = self.fin_repo.get_latest(stock.id)
            if not latest:
                return {"symbol": symbol, "available": False}
            payload = {
                "symbol": symbol,
                "available": True,
                "source": "cache_db",
                "pe": _num(latest.pe), "pb": _num(latest.pb),
                "roe": _num(latest.roe), "roce": _num(latest.roce),
                "eps": _num(latest.eps), "debt_to_equity": _num(latest.debt_to_equity),
                "revenue": _num(latest.revenue), "net_income": _num(latest.net_income),
                "free_cash_flow": _num(latest.free_cash_flow),
                "fiscal_year": latest.fiscal_year,
            }
        else:
            self._persist(stock.id, payload)
            # Backfill the sector grouping column (used by sector-rotation analytics).
            sector = payload.get("sector")
            if sector and stock.industry != sector:
                try:
                    stock.industry = sector
                    self.db.commit()
                except Exception:
                    self.db.rollback()

        _CACHE[symbol] = (now, payload)
        return payload

    # --------------------------------------------------------------- yfinance
    def _fetch_yf(self, symbol: str) -> dict | None:
        yf_symbol = f"{symbol}.NS"
        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info or {}
        except Exception as e:
            logger.warning(f"yfinance .info failed for {symbol}: {e}")
            return None

        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None and info.get("previousClose") is None:
            return None

        price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice")) or _num(info.get("previousClose"))
        market_cap = _num(info.get("marketCap"))
        roe = _num(info.get("returnOnEquity"))
        roa = _num(info.get("returnOnAssets"))
        d2e = _num(info.get("debtToEquity"))
        fcf = _num(info.get("freeCashflow"))
        shares = _num(info.get("sharesOutstanding"))
        rev_growth = _num(info.get("revenueGrowth"))
        earn_growth = _num(info.get("earningsGrowth"))

        # ROCE approximation: prefer EBIT / (assets - current liabilities) from statements.
        roce = self._estimate_roce(ticker)
        if roce is None and roa is not None:
            roce = roa * 100 * 1.25  # rough proxy when balance sheet unavailable

        data = {
            "symbol": symbol,
            "available": True,
            "source": "yfinance",
            "price": price,
            "market_cap": market_cap,
            "pe": _num(info.get("trailingPE")),
            "forward_pe": _num(info.get("forwardPE")),
            "pb": _num(info.get("priceToBook")),
            "roe": round(roe * 100, 2) if roe is not None else None,
            "roce": round(roce, 2) if roce is not None else None,
            "eps": _num(info.get("trailingEps")),
            # yfinance debtToEquity is reported as a percentage number (e.g. 45.6 -> 0.456 ratio).
            "debt_to_equity": round(d2e / 100, 4) if d2e is not None else None,
            "revenue": _num(info.get("totalRevenue")),
            "net_income": _num(info.get("netIncomeToCommon")),
            "free_cash_flow": fcf,
            "profit_margin": round(_num(info.get("profitMargins")) * 100, 2) if _num(info.get("profitMargins")) is not None else None,
            # Recent yfinance reports dividendYield already as a percentage number.
            "dividend_yield": round(_num(info.get("dividendYield")), 2) if _num(info.get("dividendYield")) is not None else None,
            "revenue_growth": round(rev_growth * 100, 2) if rev_growth is not None else None,
            "earnings_growth": round(earn_growth * 100, 2) if earn_growth is not None else None,
            "sector": info.get("sector"),
            "industry_yf": info.get("industry"),
            "fiscal_year": datetime.date.today().year,
        }

        # DCF intrinsic value per share from free cash flow.
        data["dcf"] = self._dcf(fcf, shares, market_cap, price, earn_growth or rev_growth)

        # Quarterly trend (revenue / net income / margin).
        data["quarterly"] = self._quarterly(ticker)
        return data

    def _estimate_roce(self, ticker) -> float | None:
        """ROCE = EBIT / (Total Assets - Current Liabilities), as a percentage."""
        try:
            fin = ticker.financials
            bs = ticker.balance_sheet
            if fin is None or bs is None or fin.empty or bs.empty:
                return None
            col = fin.columns[0]
            bcol = bs.columns[0]

            def pick(frame, frame_col, keys):
                for k in keys:
                    if k in frame.index:
                        v = _num(frame.loc[k, frame_col])
                        if v is not None:
                            return v
                return None

            ebit = pick(fin, col, ["EBIT", "Ebit", "Operating Income"])
            total_assets = pick(bs, bcol, ["Total Assets"])
            cur_liab = pick(bs, bcol, ["Current Liabilities", "Total Current Liabilities"])
            if ebit is None or total_assets is None or cur_liab is None:
                return None
            capital_employed = total_assets - cur_liab
            if capital_employed <= 0:
                return None
            return ebit / capital_employed * 100
        except Exception:
            return None

    def _quarterly(self, ticker) -> list:
        try:
            qf = ticker.quarterly_financials
            if qf is None or qf.empty:
                return []
            rows = []
            for col in list(qf.columns)[:4]:
                rev = None
                ni = None
                for k in ("Total Revenue", "Operating Revenue"):
                    if k in qf.index:
                        rev = _num(qf.loc[k, col]); break
                for k in ("Net Income", "Net Income Common Stockholders"):
                    if k in qf.index:
                        ni = _num(qf.loc[k, col]); break
                opm = None
                if "Operating Income" in qf.index and rev:
                    oi = _num(qf.loc["Operating Income", col])
                    if oi is not None and rev:
                        opm = round(oi / rev * 100, 2)
                q = col.date() if hasattr(col, "date") else col
                rows.append({
                    "quarter": str(q),
                    "revenue": rev,
                    "net_income": ni,
                    "operating_margin": opm,
                })
            return rows
        except Exception:
            return []

    def _dcf(self, fcf, shares, market_cap, price, growth) -> dict | None:
        """
        Simple 5-year FCF DCF with a Gordon terminal value. Returns intrinsic value
        per share and the upside vs current price.
        """
        if not fcf or fcf <= 0 or not price or price <= 0:
            return None
        if not shares or shares <= 0:
            if market_cap and price:
                shares = market_cap / price
            else:
                return None

        g = growth if (growth is not None and -0.05 < growth < 0.25) else 0.10
        disc = _DCF_DISCOUNT
        pv = 0.0
        cf = fcf
        for yr in range(1, _DCF_HORIZON + 1):
            cf = cf * (1 + g)
            pv += cf / ((1 + disc) ** yr)
        # Terminal value (Gordon growth) discounted back.
        terminal = cf * (1 + _DCF_TERMINAL_GROWTH) / (disc - _DCF_TERMINAL_GROWTH)
        pv += terminal / ((1 + disc) ** _DCF_HORIZON)

        intrinsic_per_share = pv / shares
        upside = (intrinsic_per_share - price) / price * 100
        return {
            "intrinsic_value": round(intrinsic_per_share, 2),
            "current_price": round(price, 2),
            "upside_pct": round(upside, 2),
            "verdict": "UNDERVALUED" if upside > 15 else "OVERVALUED" if upside < -15 else "FAIRLY_VALUED",
            "growth_assumed": round(g * 100, 1),
            "discount_rate": round(disc * 100, 1),
        }

    # ----------------------------------------------------------------- persist
    def _persist(self, stock_id: int, payload: dict):
        # roe / roce columns are Numeric(5,2) -> clamp to the representable range.
        def clamp(v, lo=-999.99, hi=999.99):
            return None if v is None else max(lo, min(hi, v))
        try:
            self.fin_repo.upsert_annual(stock_id, payload.get("fiscal_year", datetime.date.today().year), {
                "revenue": payload.get("revenue"),
                "net_income": payload.get("net_income"),
                "eps": payload.get("eps"),
                "pe": payload.get("pe"),
                "pb": payload.get("pb"),
                "roe": clamp(payload.get("roe")),
                "roce": clamp(payload.get("roce")),
                "debt_to_equity": payload.get("debt_to_equity"),
                "free_cash_flow": payload.get("free_cash_flow"),
            })
            for q in payload.get("quarterly", []):
                self.fin_repo.upsert_quarterly(stock_id, q["quarter"], {
                    "revenue": q.get("revenue"),
                    "net_income": q.get("net_income"),
                    "operating_profit_margin": q.get("operating_margin"),
                })
        except Exception as e:
            logger.warning(f"Persist fundamentals failed for stock {stock_id}: {e}")
            self.db.rollback()
