"""Watch .env for a new session token and use it the instant it appears.

Breeze session tokens appear to go stale very quickly, so the gap between
pasting the token and calling generate_session matters. This polls .env and
fires within ~0.25s of a save.

On a successful session it immediately harvests and caches market data to
``data/``, so a working session is never wasted.

Usage:
    .venv/bin/python refresh_session.py
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

from breeze_connect import BreezeConnect
from dotenv import dotenv_values

ENV_PATH = Path(__file__).parent / ".env"
DATA_DIR = Path(__file__).parent / "data"
POLL_SECONDS = 0.25
MAX_WAIT_SECONDS = 30 * 60
WATCH_KEY = "BREEZE_SESSION_TOKEN"

# Codes to harvest the moment a session works. DHOTRA = Dhoot Transmission Ltd
# (NSE token 764553); the Breeze code is NOT the DHOOTTRANS chart ticker.
HARVEST_CODES = ["DHOTRA", "RELIND"]


def read_token():
    """Return the session token currently in .env, or None."""
    return (dotenv_values(ENV_PATH) or {}).get(WATCH_KEY) or None


def harvest(breeze, session_token):
    """Cache everything useful from a live session. Never let one go to waste."""
    DATA_DIR.mkdir(exist_ok=True)
    out = {"harvested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    details = breeze.get_customer_details(api_session=session_token)
    if details.get("Success"):
        out["market"] = {
            "exg_status": details["Success"].get("exg_status"),
            "exg_trade_date": details["Success"].get("exg_trade_date"),
        }
        print(f"  market state : {out['market']}")

    for code in HARVEST_CODES:
        entry = {}
        quote = breeze.get_quotes(stock_code=code, exchange_code="NSE",
                                  product_type="cash", right="others", strike_price="0")
        entry["quote"] = quote.get("Success")
        if quote.get("Success"):
            nse = [r for r in quote["Success"] if r["exchange_code"] == "NSE"]
            if nse:
                print(f"  {code} quote   : ltp={nse[0]['ltp']} ltt={nse[0]['ltt']}")
        else:
            print(f"  {code} quote   : ERROR {quote.get('Error')}")

        candles = breeze.get_historical_data_v2(
            interval="1day", from_date="2023-01-01T07:00:00.000Z",
            to_date=time.strftime("%Y-%m-%dT07:00:00.000Z"), stock_code=code,
            exchange_code="NSE", product_type="cash")
        rows = candles.get("Success") or []
        entry["candles"] = rows
        if rows:
            print(f"  {code} candles : {len(rows)} daily "
                  f"({rows[0].get('datetime')} -> {rows[-1].get('datetime')})")
        else:
            print(f"  {code} candles : NONE ({candles.get('Error')})")
        out[code] = entry

    path = DATA_DIR / "harvest.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\n  cached -> {path}")
    return out


def save_session(breeze):
    """Persist the exchanged session so it is never re-exchanged.

    The raw apisession from the login URL can only be exchanged once. What must
    be kept is the ``session_key`` that exchange returns -- it signs every later
    request and stays valid for the trading day. Delegates to breeze_client so
    this and the dashboard's session form write an identical file.
    """
    from breeze_client import SESSION_PATH, store_session

    store_session(breeze.user_id, breeze.session_key)
    print(f"  session_key PERSISTED -> {SESSION_PATH}")
    print("  (reuse this; do NOT call generate_session again)")


def try_session(token):
    """Exchange the apisession once and persist the result immediately."""
    breeze = BreezeConnect(api_key=os.environ["BREEZE_API_KEY"])
    breeze.generate_session(api_secret=os.environ["BREEZE_SECRET_KEY"], session_token=token)
    save_session(breeze)
    return breeze


def main():
    env = dotenv_values(ENV_PATH) or {}
    api_key = env.get("BREEZE_API_KEY")
    if not api_key or not env.get("BREEZE_SECRET_KEY"):
        sys.exit("BREEZE_API_KEY / BREEZE_SECRET_KEY missing from .env")
    os.environ["BREEZE_API_KEY"] = api_key
    os.environ["BREEZE_SECRET_KEY"] = env["BREEZE_SECRET_KEY"]

    print("Open this URL, log in, and copy the 'apisession' value:\n")
    print(f"  https://api.icicidirect.com/apiuser/login?api_key={quote_plus(api_key)}\n")
    print(f"Then paste it into {WATCH_KEY} in .env and save.")
    print("Watching .env — I will use the token within 0.25s of your save.")
    print("(Ctrl-C to stop.)\n")

    baseline = read_token()
    print(f"current token: {baseline}  (waiting for it to change)\n")

    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        if time.monotonic() > deadline:
            print(f"Gave up after {MAX_WAIT_SECONDS // 60} min with no new token.")
            return 1
        token = read_token()
        if token and token != baseline:
            elapsed = 0.0
            print(f"[{time.strftime('%H:%M:%S')}] new token detected -> trying immediately")
            start = time.monotonic()
            try:
                breeze = try_session(token)
                elapsed = time.monotonic() - start
                print(f"  SESSION OK in {elapsed:.2f}s (user_id={breeze.user_id})\n")
                harvest(breeze, token)
                print("\nDone. H2 (short validity window) is CONFIRMED — "
                      "speed was what mattered.")
                return 0
            except Exception as exc:
                elapsed = time.monotonic() - start
                print(f"  FAILED after {elapsed:.2f}s: {exc}")
                print("  -> Near-zero latency still failed, so this is not a timing"
                      " problem. Points to H1 (server-side fault).\n")
                baseline = token
                print("Still watching in case you want to try another token.\n")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
