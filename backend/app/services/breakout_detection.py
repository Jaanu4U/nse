"""
Breakout detection engine.

Provides:
  * BREAKOUT_CATALOG - reference metadata (advantage / disadvantage / how it works)
    for every breakout pattern in the beginner / intermediate / advanced taxonomy.
  * BreakoutDetectionEngine - algorithmic detectors that work off daily OHLCV +
    swing pivots, a per-stock "breakout readiness" score, and a market-wide scanner
    that surfaces stocks currently coiled and ready to break out.

All detection is geometric / statistical on price+volume; no extra data feed needed.
"""
import logging
import datetime
import time
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository
from app.services.supply_demand import SupplyDemandEngine

logger = logging.getLogger(__name__)

# In-memory TTL cache for the full-universe breakout scan (keyed by min_readiness).
_SCAN_CACHE: dict = {}
_SCAN_TTL_SECONDS = 900  # 15 minutes; data updates daily so this is plenty fresh.


# --------------------------------------------------------------------------- #
# Reference catalog: name, category, signal, advantage, disadvantage           #
# --------------------------------------------------------------------------- #
BREAKOUT_CATALOG = {
    # ---------------------------- BEGINNER ---------------------------------- #
    "ASCENDING_TRIANGLE": {
        "label": "Ascending Triangle", "category": "beginner", "signal": "BULLISH",
        "advantage": "Clear flat resistance and rising support give an exact entry trigger and tight stop; high success rate in uptrends.",
        "disadvantage": "Prone to false breakouts on low volume; can break downward if the broader trend reverses.",
    },
    "DESCENDING_TRIANGLE": {
        "label": "Descending Triangle", "category": "beginner", "signal": "BEARISH",
        "advantage": "Reliable continuation pattern in downtrends with a defined breakdown level and measurable target.",
        "disadvantage": "Bear traps are common; in strong bull markets it often resolves upward instead.",
    },
    "SYMMETRICAL_TRIANGLE": {
        "label": "Symmetrical Triangle", "category": "beginner", "signal": "NEUTRAL",
        "advantage": "Captures coiling energy; a clean break of either trendline gives a strong directional move.",
        "disadvantage": "Direction is unknown until the break, so it requires waiting; whipsaws near the apex are frequent.",
    },
    "RECTANGLE": {
        "label": "Rectangle Breakout", "category": "beginner", "signal": "NEUTRAL",
        "advantage": "Well-defined horizontal support/resistance make entries, stops and targets simple to plan.",
        "disadvantage": "Range can persist longer than expected; repeated fakeouts at the boundaries.",
    },
    "CUP_AND_HANDLE": {
        "label": "Cup and Handle", "category": "beginner", "signal": "BULLISH",
        "advantage": "One of the most reliable continuation bases; the handle offers a low-risk pivot entry.",
        "disadvantage": "Takes weeks/months to form; a too-deep or V-shaped cup lowers reliability.",
    },
    "DOUBLE_BOTTOM": {
        "label": "Double Bottom", "category": "beginner", "signal": "BULLISH",
        "advantage": "Strong reversal signal at support with a clear neckline trigger and defined risk.",
        "disadvantage": "Fails if the neckline isn't reclaimed on volume; second bottom may undercut and trap buyers.",
    },
    "DOUBLE_TOP": {
        "label": "Double Top", "category": "beginner", "signal": "BEARISH",
        "advantage": "Early warning of a top with a measurable downside target once the neckline breaks.",
        "disadvantage": "Premature shorts get squeezed if price makes a third push to new highs.",
    },
    "INVERSE_HEAD_AND_SHOULDERS": {
        "label": "Inverse Head & Shoulders", "category": "beginner", "signal": "BULLISH",
        "advantage": "High-probability bottom reversal; neckline break with volume gives a strong, measurable rally.",
        "disadvantage": "Complex to confirm; needs a clean neckline and symmetry or reliability drops.",
    },
    "HEAD_AND_SHOULDERS": {
        "label": "Head & Shoulders", "category": "beginner", "signal": "BEARISH",
        "advantage": "Classic, well-respected topping pattern with a clear neckline target.",
        "disadvantage": "Can take long to form and is invalidated quickly if the right shoulder exceeds the head.",
    },
    # -------------------------- INTERMEDIATE -------------------------------- #
    "BULL_FLAG": {
        "label": "Bull Flag", "category": "intermediate", "signal": "BULLISH",
        "advantage": "Continuation after a sharp pole; tight, low-volume pullback offers an excellent risk/reward entry.",
        "disadvantage": "If the flag drifts down too far or too long it morphs into a reversal.",
    },
    "BEAR_FLAG": {
        "label": "Bear Flag", "category": "intermediate", "signal": "BEARISH",
        "advantage": "Reliable continuation lower with a defined breakdown trigger after a sharp drop.",
        "disadvantage": "Sharp short-covering rallies can invalidate it quickly.",
    },
    "BULL_PENNANT": {
        "label": "Bull Pennant", "category": "intermediate", "signal": "BULLISH",
        "advantage": "Energy coils into a small symmetrical triangle after a strong move; explosive continuation.",
        "disadvantage": "Short formation window; easy to misread vs a flag and gets faked near the apex.",
    },
    "BEAR_PENNANT": {
        "label": "Bear Pennant", "category": "intermediate", "signal": "BEARISH",
        "advantage": "Tight consolidation after a drop gives a clean continuation-lower trigger.",
        "disadvantage": "Prone to bear traps; needs volume confirmation on the breakdown.",
    },
    "RISING_WEDGE": {
        "label": "Rising Wedge Breakdown", "category": "intermediate", "signal": "BEARISH",
        "advantage": "Spots exhausting uptrends early; breakdown can be fast and rewarding.",
        "disadvantage": "Counter-trend by nature, so it fails more often in strong bull runs.",
    },
    "FALLING_WEDGE": {
        "label": "Falling Wedge Breakout", "category": "intermediate", "signal": "BULLISH",
        "advantage": "Bullish reversal/continuation with declining selling pressure; strong pop on the break.",
        "disadvantage": "Can keep grinding lower before breaking; requires patience and volume confirmation.",
    },
    "ROUNDING_BOTTOM": {
        "label": "Rounding Bottom", "category": "intermediate", "signal": "BULLISH",
        "advantage": "Gradual, durable accumulation base that leads to sustained advances.",
        "disadvantage": "Very slow to form and easy to enter too early before the breakout.",
    },
    "ROUNDING_TOP": {
        "label": "Rounding Top", "category": "intermediate", "signal": "BEARISH",
        "advantage": "Signals slow distribution and an impending decline.",
        "disadvantage": "Ambiguous while forming; gives back gains if you wait too long to act.",
    },
    "VOLATILITY_SQUEEZE": {
        "label": "Volatility Squeeze", "category": "intermediate", "signal": "NEUTRAL",
        "advantage": "Bollinger-inside-Keltner squeeze pinpoints compressed energy before an explosive move.",
        "disadvantage": "Tells you a move is coming but not the direction; needs a trigger to confirm.",
    },
    "GAP_BREAKOUT": {
        "label": "Gap Breakout", "category": "intermediate", "signal": "BULLISH",
        "advantage": "A volume gap above resistance shows strong demand and often runs far intraday.",
        "disadvantage": "Gaps can fill quickly; chasing leads to poor entries and wide stops.",
    },
    "TRENDLINE_BREAKOUT": {
        "label": "Trendline Breakout", "category": "intermediate", "signal": "BULLISH",
        "advantage": "Breaking a well-tested descending trendline signals a clean trend change.",
        "disadvantage": "Trendlines are subjective; minor breaks often fail without volume.",
    },
    # ---------------------------- ADVANCED ---------------------------------- #
    "VCP": {
        "label": "Volatility Contraction Pattern (VCP)", "category": "advanced", "signal": "BULLISH",
        "advantage": "Minervini's setup: successive tighter pullbacks + volume dry-up precede powerful, low-risk breakouts.",
        "disadvantage": "Hard to identify precisely; demands strict volume/contraction criteria or you get false reads.",
    },
    "DARVAS_BOX": {
        "label": "Darvas Box Breakout", "category": "advanced", "signal": "BULLISH",
        "advantage": "Mechanical box rules give objective entries above the box high and stops below the box low.",
        "disadvantage": "Generates whipsaws in choppy markets; lags at the very start of a move.",
    },
    "POCKET_PIVOT": {
        "label": "Pocket Pivot", "category": "advanced", "signal": "BULLISH",
        "advantage": "O'Neil early-entry: up-day volume exceeding recent down-volume gets you in before the obvious breakout.",
        "disadvantage": "Early entries inside a base carry higher failure risk if the base isn't sound.",
    },
    "BASE_ON_BASE": {
        "label": "Base-on-Base Breakout", "category": "advanced", "signal": "BULLISH",
        "advantage": "Stacked bases show strong relative strength and resilience, often preceding big bull-market runs.",
        "disadvantage": "Rare; can look like stalling and shakes out impatient holders.",
    },
    "HIGH_TIGHT_FLAG": {
        "label": "High Tight Flag", "category": "advanced", "signal": "BULLISH",
        "advantage": "One of the most powerful patterns: an 80-100% surge then a shallow flag often doubles again.",
        "disadvantage": "Extremely rare and volatile; wide stops and sharp reversals make sizing hard.",
    },
    "IPO_BASE": {
        "label": "IPO Base Breakout", "category": "advanced", "signal": "BULLISH",
        "advantage": "Catches fresh leadership early; first-base breakouts in new issues can run dramatically.",
        "disadvantage": "Short trading history means little support data and very high volatility.",
    },
    "WYCKOFF_SPRING": {
        "label": "Wyckoff Spring", "category": "advanced", "signal": "BULLISH",
        "advantage": "A false break below support that quickly reclaims traps sellers and launches strong upside.",
        "disadvantage": "Risky to distinguish from a genuine breakdown; needs fast confirmation.",
    },
    "WYCKOFF_SOS": {
        "label": "Wyckoff Sign of Strength (SOS)", "category": "advanced", "signal": "BULLISH",
        "advantage": "Breakout above the trading range on heavy institutional volume confirms accumulation is complete.",
        "disadvantage": "Often arrives after a big move, so entries can be extended with higher risk.",
    },
}


def _linfit(y: np.ndarray):
    """Return (slope, intercept, r2) of a least-squares line over index 0..n-1."""
    n = len(y)
    if n < 2:
        return 0.0, float(y[0]) if n else 0.0, 0.0
    x = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fit = slope * x + intercept
    ss_res = np.sum((y - fit) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), float(intercept), float(r2)


class BreakoutDetectionEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)
        self.sd = SupplyDemandEngine(db)

    # ------------------------------------------------------------------ utils
    def _enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        h, l, c = df["high"], df["low"], df["close"]
        prev = c.shift(1)
        tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
        df["atr"] = tr.rolling(14).mean()
        df["atr_pct"] = df["atr"] / c
        df["sma20"] = c.rolling(20).mean()
        df["std20"] = c.rolling(20).std()
        df["vol_sma20"] = df["volume"].rolling(20).mean()
        df["vol_sma50"] = df["volume"].rolling(50).mean()
        df["ema10"] = c.ewm(span=10, adjust=False).mean()
        df["ema50"] = c.ewm(span=50, adjust=False).mean()
        return df

    # -------------------------------------------------------- individual detectors
    def _detect(self, symbol: str, df: pd.DataFrame, swings: dict) -> list:
        out = []
        n = len(df)
        if n < 40:
            return out
        c = df["close"].values
        last = c[-1]
        highs = swings["swing_highs"]
        lows = swings["swing_lows"]

        def add(name, conf, status, level=None, stop=None, extra=None):
            meta = BREAKOUT_CATALOG.get(name, {})
            rec = {
                "pattern": name,
                "label": meta.get("label", name),
                "category": meta.get("category", "n/a"),
                "signal": meta.get("signal", "NEUTRAL"),
                "advantage": meta.get("advantage", ""),
                "disadvantage": meta.get("disadvantage", ""),
                "confidence": round(float(conf), 1),
                "status": status,  # FORMING | READY | BREAKOUT
            }
            if level is not None:
                rec["breakout_level"] = round(float(level), 2)
            if stop is not None:
                rec["stop"] = round(float(stop), 2)
            if extra:
                rec.update(extra)
            out.append(rec)

        # ---- Volatility Squeeze (Bollinger inside Keltner) ----
        row = df.iloc[-1]
        if pd.notna(row["std20"]) and pd.notna(row["atr"]):
            bb_upper = row["sma20"] + 2 * row["std20"]
            bb_lower = row["sma20"] - 2 * row["std20"]
            kc_upper = row["sma20"] + 1.5 * row["atr"]
            kc_lower = row["sma20"] - 1.5 * row["atr"]
            if bb_upper < kc_upper and bb_lower > kc_lower:
                # tighter squeeze => higher confidence
                tightness = 1 - (bb_upper - bb_lower) / (kc_upper - kc_lower + 1e-9)
                add("VOLATILITY_SQUEEZE", 60 + 35 * tightness, "READY", level=float(df["high"].iloc[-10:].max()))

        # ---- Recent consolidation box (Rectangle / Darvas) ----
        win = df.iloc[-20:]
        box_high = win["high"].max()
        box_low = win["low"].min()
        box_range = (box_high - box_low) / box_low if box_low > 0 else 1
        hi_slope, _, hi_r2 = _linfit(win["high"].values)
        lo_slope, _, lo_r2 = _linfit(win["low"].values)
        norm_hi = hi_slope / last
        norm_lo = lo_slope / last
        if box_range < 0.10 and abs(norm_hi) < 0.001 and abs(norm_lo) < 0.001:
            status = "BREAKOUT" if last >= box_high * 0.998 else "READY"
            add("DARVAS_BOX", 70 + (0.10 - box_range) * 200, status, level=box_high, stop=box_low,
                extra={"box_high": round(box_high, 2), "box_low": round(box_low, 2)})
            add("RECTANGLE", 65 + (0.10 - box_range) * 150, status, level=box_high, stop=box_low)

        # ---- Triangles (need >=2 swing highs and lows in window) ----
        rh = [h for h in highs if h["index"] >= n - 40]
        rl = [l for l in lows if l["index"] >= n - 40]
        if len(rh) >= 2 and len(rl) >= 2:
            hs = np.array([h["price"] for h in rh])
            ls = np.array([l["price"] for l in rl])
            h_sl, _, _ = _linfit(hs)
            l_sl, _, _ = _linfit(ls)
            h_sl_n, l_sl_n = h_sl / last, l_sl / last
            flat = 0.0008
            res_level = hs.max()
            sup_level = ls.min()
            # Ascending: flat highs, rising lows
            if abs(h_sl_n) < flat and l_sl_n > flat:
                status = "BREAKOUT" if last >= res_level * 0.998 else "FORMING"
                add("ASCENDING_TRIANGLE", 75, status, level=res_level, stop=sup_level)
            # Descending: flat lows, falling highs
            elif abs(l_sl_n) < flat and h_sl_n < -flat:
                add("DESCENDING_TRIANGLE", 72, "FORMING", level=sup_level, stop=res_level)
            # Symmetrical: highs down, lows up (converging)
            elif h_sl_n < -flat and l_sl_n > flat:
                status = "BREAKOUT" if last >= res_level * 0.998 else "FORMING"
                add("SYMMETRICAL_TRIANGLE", 68, status, level=res_level, stop=sup_level)
            # Rising wedge: both up, highs slower than lows (bearish)
            elif h_sl_n > flat and l_sl_n > flat and l_sl_n > h_sl_n:
                add("RISING_WEDGE", 64, "FORMING", level=sup_level, stop=res_level)
            # Falling wedge: both down, lows slower than highs (bullish)
            elif h_sl_n < -flat and l_sl_n < -flat and abs(l_sl_n) < abs(h_sl_n):
                add("FALLING_WEDGE", 66, "FORMING", level=res_level, stop=sup_level)

        # ---- Flags & Pennants (pole + consolidation) ----
        if n >= 30:
            pole = df.iloc[-30:-10]
            cons = df.iloc[-10:]
            pole_ret = (pole["close"].iloc[-1] - pole["close"].iloc[0]) / pole["close"].iloc[0]
            cons_high = cons["high"].max()
            cons_low = cons["low"].min()
            cons_range = (cons_high - cons_low) / cons_low if cons_low > 0 else 1
            vol_pole = pole["volume"].mean()
            vol_cons = cons["volume"].mean()
            vol_dry = vol_cons < vol_pole * 0.9
            ch_sl, _, _ = _linfit(cons["high"].values)
            cl_sl, _, _ = _linfit(cons["low"].values)
            if pole_ret > 0.15 and cons_range < 0.12 and vol_dry:
                # downward/sideways drift = flag; converging = pennant
                if (ch_sl / last) < -0.0005 and (cl_sl / last) > 0.0005:
                    add("BULL_PENNANT", 70, "READY", level=cons_high, stop=cons_low)
                else:
                    status = "BREAKOUT" if last >= cons_high * 0.998 else "READY"
                    add("BULL_FLAG", 74, status, level=cons_high, stop=cons_low)
                # High Tight Flag: huge run + shallow base
                run8w = (last - df["close"].iloc[-40]) / df["close"].iloc[-40] if n >= 40 else 0
                if run8w > 0.8 and cons_range < 0.25:
                    add("HIGH_TIGHT_FLAG", 80, "READY", level=cons_high, stop=cons_low)
            elif pole_ret < -0.15 and cons_range < 0.12 and vol_dry:
                if (ch_sl / last) > 0.0005 and (cl_sl / last) < -0.0005:
                    add("BEAR_PENNANT", 66, "FORMING", level=cons_low, stop=cons_high)
                else:
                    add("BEAR_FLAG", 68, "FORMING", level=cons_low, stop=cons_high)

        # ---- VCP: successive contractions + volume dry-up ----
        vcp = self._detect_vcp(df, highs, lows)
        if vcp:
            add("VCP", vcp["confidence"], vcp["status"], level=vcp["level"], stop=vcp["stop"],
                extra={"contractions": vcp["contractions"]})

        # ---- Cup & Handle / Rounding Bottom (parabola) ----
        seg = df["close"].iloc[-60:].values if n >= 60 else df["close"].values
        if len(seg) >= 40:
            xs = np.arange(len(seg), dtype=float)
            a, b, cc = np.polyfit(xs, seg, 2)
            curv = a / last
            fit = a * xs ** 2 + b * xs + cc
            ss_res = np.sum((seg - fit) ** 2)
            ss_tot = np.sum((seg - seg.mean()) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            vertex = -b / (2 * a) if a != 0 else -1
            if curv > 1e-6 and r2 > 0.6 and 0.2 * len(seg) < vertex < 0.8 * len(seg):
                prior_high = seg[:5].max()
                if last >= prior_high * 0.9:
                    add("CUP_AND_HANDLE", 60 + 30 * r2, "READY", level=float(seg.max()), stop=float(seg.min()))
                add("ROUNDING_BOTTOM", 55 + 30 * r2, "FORMING", level=float(seg.max()))
            elif curv < -1e-6 and r2 > 0.6:
                add("ROUNDING_TOP", 55 + 30 * r2, "FORMING")

        # ---- Gap breakout ----
        if n >= 2:
            prev_high = df["high"].iloc[-2]
            today_low = df["low"].iloc[-1]
            if today_low > prev_high and row["volume"] > 1.5 * (row["vol_sma20"] or row["volume"]):
                add("GAP_BREAKOUT", 72, "BREAKOUT", level=prev_high, stop=today_low)

        # ---- 52-week / range high breakout = Wyckoff SOS ----
        lookback_high = df["high"].iloc[:-1].max()
        if last >= lookback_high and pd.notna(row["vol_sma50"]) and row["volume"] > 1.5 * row["vol_sma50"]:
            add("WYCKOFF_SOS", 78, "BREAKOUT", level=lookback_high, stop=float(df["low"].iloc[-10:].min()))

        # ---- Pocket Pivot ----
        if n >= 11:
            last10 = df.iloc[-11:-1]
            down_vol = last10.loc[last10["close"] < last10["close"].shift(1), "volume"]
            max_down_vol = down_vol.max() if not down_vol.empty else 0
            up_day = last > c[-2]
            near_ema = last >= row["ema50"] * 0.97 if pd.notna(row["ema50"]) else False
            if up_day and near_ema and row["volume"] > max_down_vol and max_down_vol > 0:
                add("POCKET_PIVOT", 65, "READY", level=float(df["high"].iloc[-1]),
                    stop=float(row["ema10"]) if pd.notna(row["ema10"]) else None)

        # ---- Trendline breakout (break descending swing-high line) ----
        if len(rh) >= 3:
            hs = np.array([h["price"] for h in rh[-3:]])
            sl, ic, _ = _linfit(hs)
            projected = sl * (len(hs) - 1 + 1) + ic  # one step beyond last swing high
            if sl < 0 and last > projected:
                add("TRENDLINE_BREAKOUT", 64, "BREAKOUT", level=float(projected))

        # ---- Wyckoff Spring (false breakdown reclaimed) ----
        if len(rl) >= 1 and n >= 10:
            support = rl[-1]["price"]
            recent = df.iloc[-5:]
            pierced = (recent["low"] < support * 0.99).any()
            reclaimed = last > support
            if pierced and reclaimed:
                add("WYCKOFF_SPRING", 67, "READY", level=float(df["high"].iloc[-5:].max()), stop=float(support * 0.97))

        return out

    def _detect_vcp(self, df: pd.DataFrame, highs: list, lows: list) -> dict:
        """
        Volatility Contraction Pattern: a sequence of pullbacks where each
        successive pullback is shallower than the last, with declining volume.
        """
        n = len(df)
        piv = sorted(
            [{"i": h["index"], "p": h["price"], "t": "H"} for h in highs if h["index"] >= n - 80] +
            [{"i": l["index"], "p": l["price"], "t": "L"} for l in lows if l["index"] >= n - 80],
            key=lambda d: d["i"],
        )
        # Measure pullback depths from each swing high to the following swing low.
        depths = []
        for k in range(len(piv) - 1):
            if piv[k]["t"] == "H" and piv[k + 1]["t"] == "L":
                hp, lp = piv[k]["p"], piv[k + 1]["p"]
                if hp > 0:
                    depths.append((hp - lp) / hp)
        if len(depths) < 2:
            return None
        last_depths = depths[-3:] if len(depths) >= 3 else depths
        # Require monotonic contraction and a tight final pullback.
        contracting = all(last_depths[i] > last_depths[i + 1] for i in range(len(last_depths) - 1))
        if not contracting or last_depths[-1] > 0.10:
            return None
        # Volume dry-up: recent 10-day vol below 50-day average.
        vol_recent = df["volume"].iloc[-10:].mean()
        vol_base = df["volume"].iloc[-50:].mean() if n >= 50 else df["volume"].mean()
        if vol_recent >= vol_base:
            return None
        pivot_high = df["high"].iloc[-15:].max()
        last = df["close"].iloc[-1]
        status = "BREAKOUT" if last >= pivot_high * 0.998 else "READY"
        conf = 70 + (len(last_depths) - 1) * 6 + (0.10 - last_depths[-1]) * 100
        return {
            "confidence": min(conf, 95),
            "status": status,
            "level": pivot_high,
            "stop": df["low"].iloc[-10:].min(),
            "contractions": [round(d * 100, 1) for d in last_depths],
        }

    # ----------------------------------------------------- public: per stock
    def detect_for_symbol(self, symbol: str) -> dict:
        df = self.sd._get_price_df(symbol, lookback_days=400)
        if df is None or len(df) < 40:
            return {"symbol": symbol.upper(), "patterns": [], "readiness": 0, "breakout_levels": {}}
        df = self._enrich(df)
        swings = self.sd.find_swings(df, window=5)
        patterns = self._detect(symbol, df, swings)
        readiness = self._readiness(df, patterns)
        last = float(df["close"].iloc[-1])
        # Nearest bullish breakout level above current price.
        bull_levels = [p["breakout_level"] for p in patterns
                       if p.get("signal") == "BULLISH" and p.get("breakout_level", 0) >= last]
        nearest = min(bull_levels) if bull_levels else None
        return {
            "symbol": symbol.upper(),
            "price": round(last, 2),
            "readiness": readiness,
            "patterns": sorted(patterns, key=lambda p: p["confidence"], reverse=True),
            "nearest_breakout": round(nearest, 2) if nearest else None,
            "pct_to_breakout": round((nearest - last) / last * 100, 2) if nearest else None,
        }

    def _readiness(self, df: pd.DataFrame, patterns: list) -> int:
        """0-100 score: how coiled & ready a stock is to break out (bullish bias)."""
        score = 0.0
        row = df.iloc[-1]
        last = row["close"]
        # Squeeze on
        if any(p["pattern"] == "VOLATILITY_SQUEEZE" for p in patterns):
            score += 25
        # Tight range last 20 days
        win = df.iloc[-20:]
        rng = (win["high"].max() - win["low"].min()) / win["low"].min() if win["low"].min() > 0 else 1
        score += max(0, (0.15 - rng) / 0.15) * 20
        # Volume dry-up
        if pd.notna(row["vol_sma20"]) and pd.notna(row["vol_sma50"]) and row["vol_sma50"] > 0:
            if row["vol_sma20"] < row["vol_sma50"]:
                score += 12
        # Proximity to recent high
        hi = df["high"].iloc[-60:].max()
        if hi > 0:
            prox = 1 - min(abs(hi - last) / hi, 0.15) / 0.15
            score += prox * 18
        # Bullish setups detected
        bull = [p for p in patterns if p["signal"] == "BULLISH"]
        ready_or_break = [p for p in patterns if p["status"] in ("READY", "BREAKOUT")]
        score += min(len(bull) * 6, 15)
        score += min(len(ready_or_break) * 5, 10)
        return int(min(round(score), 100))

    # ----------------------------------------------- public: market scanner
    def scan_breakout_ready(self, limit: int = 30, min_readiness: int = 55) -> list:
        """
        Scan stocks that already have recent indicator/prediction coverage and
        return those most ready to break out, ranked by readiness score.
        Only considers stocks with recent price data to keep the scan fast.
        Results are cached for a short TTL so the panel loads instantly on
        repeat visits instead of re-running the full-universe scan every time.
        """
        from app.models.models import Stock, PriceDaily
        from sqlalchemy import func

        # Serve from cache when fresh (full ranked list cached; slice per request).
        now = time.time()
        cached = _SCAN_CACHE.get(min_readiness)
        if cached and (now - cached["ts"]) < _SCAN_TTL_SECONDS:
            return cached["data"][:limit]

        cutoff = datetime.date.today() - datetime.timedelta(days=7)
        # Stocks with a recent candle and enough history.
        sub = (
            self.db.query(
                PriceDaily.stock_id.label("sid"),
                func.max(PriceDaily.timestamp).label("mx"),
                func.count().label("cnt"),
            ).group_by(PriceDaily.stock_id).subquery()
        )
        rows = (
            self.db.query(Stock.symbol)
            .join(sub, sub.c.sid == Stock.id)
            .filter(Stock.is_active == True, sub.c.mx >= cutoff, sub.c.cnt >= 60)
            .all()
        )
        results = []
        for (symbol,) in rows:
            try:
                res = self.detect_for_symbol(symbol)
            except Exception as e:
                logger.debug(f"breakout scan failed for {symbol}: {e}")
                continue
            if res["readiness"] >= min_readiness and res["patterns"]:
                top = res["patterns"][0]
                results.append({
                    "symbol": res["symbol"],
                    "price": res["price"],
                    "readiness": res["readiness"],
                    "top_pattern": top["label"],
                    "top_pattern_key": top["pattern"],
                    "signal": top["signal"],
                    "status": top["status"],
                    "confidence": top["confidence"],
                    "nearest_breakout": res["nearest_breakout"],
                    "pct_to_breakout": res["pct_to_breakout"],
                    "pattern_count": len(res["patterns"]),
                })
        results.sort(key=lambda r: (r["readiness"], r["confidence"]), reverse=True)
        _SCAN_CACHE[min_readiness] = {"ts": now, "data": results}
        return results[:limit]
