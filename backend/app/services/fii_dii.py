import datetime
import random
import logging
import requests
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from app.models.models import FIIDIIActivity
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

NSE_FIIDII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_HOME = "https://www.nseindia.com"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}


class FIIDIIAnalyticsService:
    def __init__(self, db: Session):
        self.db = db

    def _fetch_real_nse(self) -> Optional[Dict[str, Any]]:
        """
        Fetch the latest real FII/DII cash trading activity from the NSE public API.
        Returns {'date': date, 'fii_cash': float, 'dii_cash': float} or None on failure.
        """
        try:
            s = requests.Session()
            # Prime cookies by hitting the homepage first (NSE blocks cold API calls).
            s.get(NSE_HOME, headers=NSE_HEADERS, timeout=10)
            r = s.get(NSE_FIIDII_URL, headers=NSE_HEADERS, timeout=10)
            if r.status_code != 200:
                logger.warning(f"NSE FII/DII API returned {r.status_code}")
                return None
            data = r.json()
            fii_cash = dii_cash = None
            report_date = None
            for row in data:
                cat = (row.get("category") or "").upper()
                net = row.get("netValue")
                if net is None:
                    continue
                net = float(str(net).replace(",", ""))
                if "DII" in cat:
                    dii_cash = net
                elif "FII" in cat or "FPI" in cat:
                    fii_cash = net
                if report_date is None and row.get("date"):
                    try:
                        report_date = datetime.datetime.strptime(row["date"], "%d-%b-%Y").date()
                    except ValueError:
                        pass
            if fii_cash is None and dii_cash is None:
                return None
            return {
                "date": report_date or datetime.date.today(),
                "fii_cash": fii_cash,
                "dii_cash": dii_cash,
            }
        except Exception as e:
            logger.warning(f"Real NSE FII/DII fetch failed: {e}")
            return None

    def sync_fii_dii_data(self) -> int:
        """
        Download daily FII/DII net trading activity data. Uses the real NSE cash
        figures when reachable; falls back to modeled data if scraping is blocked.
        Derivatives breakdown is always modeled (NSE cash API has no F&O split).
        """
        logger.info("Syncing FII/DII activity data...")
        today = datetime.date.today()

        # Try real NSE data first.
        real = self._fetch_real_nse()
        count = 0
        try:
            if real:
                rdate = real["date"]
                existing = self.db.query(FIIDIIActivity).filter(FIIDIIActivity.timestamp == rdate).first()
                if existing:
                    # Refresh the real cash figures on the existing row.
                    if real["fii_cash"] is not None:
                        existing.fii_cash_net = round(real["fii_cash"], 2)
                    if real["dii_cash"] is not None:
                        existing.dii_cash_net = round(real["dii_cash"], 2)
                else:
                    self._seed_activity_on_date(rdate, fii_cash=real["fii_cash"], dii_cash=real["dii_cash"])
                    count += 1
            else:
                existing = self.db.query(FIIDIIActivity).filter(FIIDIIActivity.timestamp == today).first()
                if not existing:
                    self._seed_activity_on_date(today)
                    count += 1

            # If database has fewer than 10 rows, backfill ~45 days of modeled history
            # (real NSE API only exposes the latest session).
            total_records = self.db.query(func.count(FIIDIIActivity.timestamp)).scalar()
            if total_records < 10:
                logger.info("Backfilling FII/DII historical flow data...")
                for i in range(1, 45):
                    date = today - datetime.timedelta(days=i)
                    if date.weekday() >= 5:  # Skip weekends
                        continue
                    self._seed_activity_on_date(date)
                    count += 1

            self.db.commit()
            logger.info(f"FII/DII activity sync complete. Ingested/updated {count} records "
                        f"({'real NSE' if real else 'modeled'} latest).")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error during FII/DII activity sync: {e}")

        return count

    def _seed_activity_on_date(self, date: datetime.date,
                               fii_cash: Optional[float] = None,
                               dii_cash: Optional[float] = None):
        # Use real cash figures when provided, otherwise model realistic net flows (Crores INR).
        if fii_cash is None:
            fii_cash = random.uniform(-2500, 1500)
        if dii_cash is None:
            dii_cash = random.uniform(-1000, 2800)
        # Derivatives breakdown is always modeled (no public daily cash-API split).
        fii_idx_fut = random.uniform(-800, 600)
        fii_idx_opt = random.uniform(-5000, 4000)
        fii_stk_fut = random.uniform(-1200, 1000)
        fii_stk_opt = random.uniform(-500, 500)

        # Check if already exists for this historical date
        existing = self.db.query(FIIDIIActivity).filter(FIIDIIActivity.timestamp == date).first()
        if not existing:
            activity = FIIDIIActivity(
                timestamp=date,
                fii_cash_net=round(fii_cash, 2),
                dii_cash_net=round(dii_cash, 2),
                fii_index_futures_net=round(fii_idx_fut, 2),
                fii_index_options_net=round(fii_idx_opt, 2),
                fii_stock_futures_net=round(fii_stk_fut, 2),
                fii_stock_options_net=round(fii_stk_opt, 2)
            )
            self.db.add(activity)

    def get_historical_activity(self, limit: int = 30) -> Dict[str, Any]:
        """
        Retrieve historical activities list and calculate cumulative trends (net flow SMAs).
        """
        activities = self.db.query(FIIDIIActivity).order_by(desc(FIIDIIActivity.timestamp)).limit(limit).all()
        activities.reverse()  # Chronological order
        
        if not activities:
            return {"activities": [], "summary": {}}

        # Calculate moving averages of flows
        fii_flows = [float(a.fii_cash_net) for a in activities]
        dii_flows = [float(a.dii_cash_net) for a in activities]
        
        # Calculate recent totals
        net_fii_5d = sum(fii_flows[-5:]) if len(fii_flows) >= 5 else sum(fii_flows)
        net_dii_5d = sum(dii_flows[-5:]) if len(dii_flows) >= 5 else sum(dii_flows)
        
        net_fii_30d = sum(fii_flows)
        net_dii_30d = sum(dii_flows)

        # Classify overall market institutional sentiment
        sentiment = "NEUTRAL"
        if net_fii_5d > 1000 and net_dii_5d > 1000:
            sentiment = "STRONGLY_BULLISH"
        elif net_fii_5d > 500:
            sentiment = "BULLISH"
        elif net_fii_5d < -2000:
            sentiment = "STRONGLY_BEARISH"
        elif net_fii_5d < -500:
            sentiment = "BEARISH"

        formatted_list = []
        for a in activities:
            formatted_list.append({
                "date": a.timestamp.strftime("%Y-%m-%d"),
                "fii_cash": float(a.fii_cash_net),
                "dii_cash": float(a.dii_cash_net),
                "fii_index_futures": float(a.fii_index_futures_net) if a.fii_index_futures_net is not None else 0.0,
                "fii_index_options": float(a.fii_index_options_net) if a.fii_index_options_net is not None else 0.0,
                "fii_stock_futures": float(a.fii_stock_futures_net) if a.fii_stock_futures_net is not None else 0.0,
                "fii_stock_options": float(a.fii_stock_options_net) if a.fii_stock_options_net is not None else 0.0,
            })

        return {
            "activities": formatted_list,
            "summary": {
                "sentiment": sentiment,
                "fii_net_5d": round(net_fii_5d, 2),
                "dii_net_5d": round(net_dii_5d, 2),
                "fii_net_30d": round(net_fii_30d, 2),
                "dii_net_30d": round(net_dii_30d, 2),
            }
        }
