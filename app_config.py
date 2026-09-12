"""App configuration.

The watchlist is managed from the UI and stored in ``data/watchlist.json``.
``WATCHLIST`` below is only the seed for a first run and is intentionally empty.

Any ``code`` used here or by the UI is the Breeze ShortName from the security
master, which is NOT the chart ticker: Reliance trades as RELIANCE but the API
wants RELIND, and Dhoot Transmission is DHOTRA rather than DHOOTTRANS.
"""

import os

# Deliberately empty. This is only the seed used when data/watchlist.json does not
# exist yet, so anything here is what a brand-new install -- or a host with an
# ephemeral disk, where the file is wiped on every restart -- silently shows as
# "your" watchlist. Hardcoding names meant a deployed instance kept resurrecting
# stocks that had been removed. A new user starts empty and is told how to add.
WATCHLIST = []

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
