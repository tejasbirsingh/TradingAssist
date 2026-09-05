"""App configuration. Edit WATCHLIST to track different symbols.

``code`` must be the Breeze ShortName from the security master, which is NOT
the chart ticker: Dhoot Transmission trades as DHOOTTRANS but the API wants
DHOTRA.
"""

import os

WATCHLIST = [
    {"code": "DHOTRA", "label": "Dhoot Transmission"},
    {"code": "SBIFUN", "label": "SBI Funds Management"},
    {"code": "RELIND", "label": "Reliance Industries"},
]

EXCHANGE_CODE = "NSE"
PRODUCT_TYPE = "cash"

TIMEFRAMES = ["1day", "30minute", "5minute"]
DEFAULT_TIMEFRAME = "1day"

# Daily history start. Breeze returns whatever exists, so a wide window is safe.
HISTORY_START = "2023-01-01"
# Intraday history is only kept for a short period upstream.
INTRADAY_DAYS = 30

IST = "Asia/Kolkata"
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)

# Breeze allows 100 calls/minute and 5000/day, so responses are cached and the
# browser polls this server rather than the broker.
QUOTE_TTL_OPEN = 20
QUOTE_TTL_CLOSED = 1800
CANDLE_TTL_OPEN = 300
CANDLE_TTL_CLOSED = 12 * 3600

# Require a Google sign-in before the dashboard is usable. Needs
# firebase-web-config.json.
#
# Overridable by the REQUIRE_LOGIN environment variable so an ungated throwaway
# instance can be started without editing this file -- doing that by hand left the
# gate switched off twice when the command around it timed out. Anything other than
# a recognised falsey word keeps the gate on, so a typo fails closed.
REQUIRE_LOGIN = (os.getenv("REQUIRE_LOGIN", "true").strip().lower()
                 not in ("false", "0", "no", "off"))
