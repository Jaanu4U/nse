"""
Simple Kite Connect wrapper for fetching NSE live data.

Usage:
- Set `KITE_API_KEY`, `KITE_API_SECRET`, and `KITE_ACCESS_TOKEN` in your .env or environment.
- Use `KiteService.get_ltp(["NSE:RELIANCE", "NSE:INFY"])` to fetch live LTPs.

This module keeps a thin synchronous wrapper around the official `kiteconnect` client.
"""
from typing import List, Dict, Any, Optional
import logging

from kiteconnect import KiteConnect
from ..config import settings

logger = logging.getLogger(__name__)


class KiteService:
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, access_token: Optional[str] = None):
        self.api_key = api_key or settings.KITE_API_KEY
        self.api_secret = api_secret or settings.KITE_API_SECRET
        self.access_token = access_token or settings.KITE_ACCESS_TOKEN
        if not self.api_key:
            raise RuntimeError("KITE_API_KEY is not configured")
        self.client = KiteConnect(api_key=self.api_key)
        if self.access_token:
            try:
                self.client.set_access_token(self.access_token)
            except Exception:
                logger.exception("Failed to set Kite access token")

    def get_ltp(self, instruments: List[str]) -> Dict[str, Any]:
        """Fetch LTP for a list of instruments. Instruments must be fully qualified e.g. 'NSE:RELIANCE'."""
        if not instruments:
            return {}
        try:
            # kiteconnect accepts a comma-separated string or a list
            return self.client.ltp(instruments)
        except Exception:
            logger.exception("Failed to fetch LTP from Kite")
            return {}

    def get_quote(self, instrument: str) -> Dict[str, Any]:
        """Fetch full quote for a single instrument (e.g. 'NSE:RELIANCE')."""
        try:
            return self.client.quote(instrument)
        except Exception:
            logger.exception("Failed to fetch quote from Kite")
            return {}

    def get_ohlc(self, instrument: str, interval: str = "day") -> Dict[str, Any]:
        """Fetch OHLC for an instrument. Interval examples: 'minute', 'day', '5minute', '15minute'."""
        try:
            return self.client.historical_data(instrument, None, None, interval)
        except Exception:
            logger.exception("Failed to fetch OHLC from Kite")
            return {}


# convenience singleton for importers
_kite = None


def get_kite_client() -> KiteService:
    global _kite
    if _kite is None:
        _kite = KiteService()
    return _kite
