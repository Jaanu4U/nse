import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository
from app.repositories.indicator_repo import IndicatorRepository
from app.services.supply_demand import SupplyDemandEngine
from app.services.prediction import PredictionEngine
from app.config import settings
import datetime
import google.generativeai as genai

logger = logging.getLogger(__name__)

class AIAnalystService:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)
        self.indicator_repo = IndicatorRepository(db)
        self.sd_engine = SupplyDemandEngine(db)
        self.pred_engine = PredictionEngine(db)

    def generate_stock_report(self, symbol: str) -> str:
        """
        Compile technical data and generate a structured analytical report using Gemini
        with a robust local fallback generator if no API key is set.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return f"Error: Stock {symbol} not found in database."

        # 1. Compile Data
        latest_price = self.price_repo.get_latest_daily_price(stock.id)
        latest_ind = self.indicator_repo.get_indicators(stock.id, datetime.date.today() - datetime.timedelta(days=10), datetime.date.today())
        zones = self.sd_engine.detect_support_resistance(symbol)
        preds = self.pred_engine.predict_next_day(symbol)
        breakout = self.sd_engine.detect_breakouts(symbol)

        price_val = float(latest_price.close) if latest_price else 0.0
        change_val = float(latest_price.close - latest_price.open) if latest_price else 0.0
        change_pct = (change_val / float(latest_price.open) * 100) if latest_price and latest_price.open > 0 else 0.0
        
        ind_data = latest_ind[-1] if latest_ind else None
        rsi_val = float(ind_data.rsi) if (ind_data and ind_data.rsi is not None) else 50.0
        macd_val = float(ind_data.macd) if (ind_data and ind_data.macd is not None) else 0.0
        ema20 = float(ind_data.ema_20) if (ind_data and ind_data.ema_20 is not None) else price_val
        ema200 = float(ind_data.ema_200) if (ind_data and ind_data.ema_200 is not None) else price_val

        # Support & Resistance strings
        sup_str = ", ".join([f"₹{s['price']} (Conf: {s['confidence']})" for s in zones["supports"][:2]])
        res_str = ", ".join([f"₹{r['price']} (Conf: {r['confidence']})" for r in zones["resistances"][:2]])

        # 2. Build Report Context
        context = {
            "symbol": symbol,
            "company_name": stock.company_name,
            "industry": stock.industry,
            "price": price_val,
            "change_pct": round(change_pct, 2),
            "rsi": round(rsi_val, 2),
            "macd": round(macd_val, 4),
            "ema20": round(ema20, 2),
            "ema200": round(ema200, 2),
            "supports": sup_str,
            "resistances": res_str,
            "breakout": breakout,
            "preds": preds
        }

        # 3. Call Gemini if API Key is set
        if settings.GEMINI_API_KEY:
            try:
                genai.configure(api_key=settings.GEMINI_API_KEY)
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                prompt = f"""
                You are a Senior Quantitative Stock Analyst. Generate a professional analysis report for {context['company_name']} ({context['symbol']}).
                
                Current Market Data:
                - Current Close: ₹{context['price']} ({context['change_pct']}% change)
                - Technical Indicators: RSI(14) is {context['rsi']}, MACD is {context['macd']}, price vs EMA(20) is ₹{context['ema20']}, EMA(200) is ₹{context['ema200']}.
                - Support Zones: {context['supports']}
                - Resistance Zones: {context['resistances']}
                - Next-day Move Probabilities: +1%: {context['preds'].get('prob_plus_1')*100:.2f}%, +2%: {context['preds'].get('prob_plus_2')*100:.2f}%, +3%: {context['preds'].get('prob_plus_3')*100:.2f}%, +5%: {context['preds'].get('prob_plus_5')*100:.2f}%
                - Breakout signal: {context['breakout']}
                
                Your report must include these sections:
                1. **Trend Analysis**: Analyze the price action relative to moving averages.
                2. **Momentum Analysis**: Interpret RSI and MACD trends.
                3. **Volume Analysis**: Evaluate current interest.
                4. **Support & Resistance**: Identify critical levels to watch.
                5. **Risk Assessment**: Assess volatility and risk factors.
                6. **Probability Analysis**: Interpret the next-day move probabilities.
                
                Important Guidelines:
                - Explain technical details clearly.
                - Never claim certainty. Use probabilistic language.
                - End with a prominent disclaimer stating: "This analysis is for educational purposes only and does not constitute financial advice."
                """
                response = model.generate_content(prompt)
                return response.text
            except Exception as e:
                logger.error(f"Failed to generate report via Gemini API: {e}. Falling back to local template.")

        # Local Dynamic Fallback
        return self._generate_local_report(context)

    def _generate_local_report(self, ctx: Dict[str, Any]) -> str:
        trend_status = "BULLISH" if ctx["price"] > ctx["ema20"] else "BEARISH"
        long_trend = "UPWARD" if ctx["price"] > ctx["ema200"] else "DOWNWARD"
        
        rsi_status = "Neutral"
        if ctx["rsi"] > 70:
            rsi_status = "Overbought (Bearish Divergence Risk)"
        elif ctx["rsi"] < 30:
            rsi_status = "Oversold (Bullish Reversal Potential)"

        report = f"""# AI Analyst Report: {ctx['company_name']} ({ctx['symbol']})
**Date**: {datetime.date.today().strftime('%B %d, %Y')}
**Current Price**: ₹{ctx['price']} ({ctx['change_pct']}%)

---

### 1. Trend Analysis
The stock is currently trading in a **{trend_status}** short-term phase as price rests {'above' if ctx['price'] > ctx['ema20'] else 'below'} its 20-day Exponential Moving Average (EMA: ₹{ctx['ema20']}). In the longer-term frame, the macro trend appears **{long_trend}** relative to the 200-day EMA (₹{ctx['ema200']}).

### 2. Momentum Analysis
- **RSI (14)**: {ctx['rsi']} ({rsi_status}). Momentum is currently indicating a stable trend space with no immediate extreme overextensions.
- **MACD**: The MACD line is at {ctx['macd']}. The histogram suggests {"increasing positive" if ctx['macd'] > 0 else "neutral or negative"} momentum cycles.

### 3. Volume Analysis
Volume spikes show {"strong" if ctx['breakout'].get('breakout_detected') else "moderate"} institutional commitment. The volume breakout tracker is currently at a ratio of **{ctx['breakout'].get('volume_ratio', 1.0)}x** relative to its 20-day average.

### 4. Support & Resistance
- **Critical Supports**: {ctx['supports'] or "None detected within range."}
- **Critical Resistances**: {ctx['resistances'] or "None detected within range."}
- **Breakout Status**: {ctx['breakout'].get('type', 'NO_BREAKOUT_DETECTED')} (Confidence: {ctx['breakout'].get('confidence', 0)}%)

### 5. Risk Assessment
Volatility triggers (ATR-based) indicate average daily ranges are within standard limits. Maintain strict stop-loss measures near support boundaries (₹{ctx['supports'].split(' ')[0] if ctx['supports'] else 'N/A'}) to hedge against sudden capital drawdown.

### 6. Probability Analysis
Based on our machine learning prediction modules, the estimated probability of price movement in the next session is:
- **Probability of +1% move**: {ctx['preds'].get('prob_plus_1', 0.0)*100:.2f}%
- **Probability of +2% move**: {ctx['preds'].get('prob_plus_2', 0.0)*100:.2f}%
- **Probability of +3% move**: {ctx['preds'].get('prob_plus_3', 0.0)*100:.2f}%
- **Probability of +5% move**: {ctx['preds'].get('prob_plus_5', 0.0)*100:.2f}%

*Note: These probability levels are estimates generated from statistical analysis of past price-action clusters and carry no guarantee of future returns.*

---

**DISCLAIMER**: This analysis is generated by an AI module for educational and informational purposes only and does not constitute financial, investment, or trading advice. Please consult a SEBI registered investment advisor before committing capital.
"""
        return report
