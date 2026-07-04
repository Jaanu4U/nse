Kite (Zerodha) integration

This service provides a thin wrapper around the `kiteconnect` client to fetch live NSE data.

Required environment variables (set in `.env` or your environment):

- `KITE_API_KEY`
- `KITE_API_SECRET`
- `KITE_ACCESS_TOKEN` (obtain via Kite Connect login flow)

Example usage inside the backend:

from app.services.kite_service import get_kite_client

kite = get_kite_client()
ltp = kite.get_ltp(["NSE:RELIANCE", "NSE:INFY"])  # returns dict keyed by instrument

Notes:
- `kiteconnect` requires you to create an API key/secret in Zerodha developer console and complete the login/token flow to obtain an access token. See the official docs: https://kite.trade/docs/connect/v3/.
- Keep `KITE_API_SECRET` and `KITE_ACCESS_TOKEN` secret (do not commit them).
