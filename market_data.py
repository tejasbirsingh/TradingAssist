"""Fetch and cache Breeze market data.

Breeze allows only 100 calls/minute and 5000/day, so everything is cached to
disk and the browser polls this server instead of the broker. A five-symbol
watchlist costs roughly 15 calls a day.
"""

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import app_config as config
from breeze_client import (SessionExpired, SessionUnavailable,
                           is_session_dead, load_client)
import dataquality
import livefeed
import splits
from signals import evaluate

CACHE_DIR = Path(__file__).parent / "data" / "cache"
_client = None
# Endpoints are sync defs, so FastAPI runs them on a threadpool and several
# snapshots can be in flight at once. Saving a session drops the memoised client
# while the page is still polling every three seconds, so without this each of
# those requests started a build of its own.
_client_lock = threading.Lock()

# Breeze stamps last-trade-time like '21-Aug-2026 15:57:49'.
LTT_FORMAT = "%d-%b-%Y %H:%M:%S"


def client():
    """Memoised Breeze client. One session per process, reused from disk.

    Built at most once even when several requests arrive together. A failed build
    is not remembered, so a session that starts working is picked up immediately.
    """
    global _client
    with _client_lock:
        if _client is None:
            _client = load_client()
        return _client


def reset_client():
    """Drop the memoised client so the next call picks up a new session key."""
    global _client
    with _client_lock:
        _client = None


def now_ist():
    return datetime.now(ZoneInfo(config.IST))


def within_market_hours(now=None):
    """Whether the clock is inside an NSE session. Needs no API call.

    Separate from market_state because cache TTLs must be chosen *before* a
    quote is fetched, while the full open/closed answer needs that quote's
    last-trade-time to spot holidays.
    """
    now = now or now_ist()
    open_at = now.replace(hour=config.MARKET_OPEN[0], minute=config.MARKET_OPEN[1],
                          second=0, microsecond=0)
    close_at = now.replace(hour=config.MARKET_CLOSE[0], minute=config.MARKET_CLOSE[1],
                           second=0, microsecond=0)
    return bool(now.weekday() < 5 and open_at <= now <= close_at)


def market_state(ltt=None, now=None):
    """Work out whether NSE is trading.

    ``get_customer_details`` would report ``exg_status`` but it needs the raw
    apisession, which is spent after the one-time exchange. So this combines
    the clock with the last-trade-time: on a holiday the clock says "hours" but
    the last trade is from a previous day, which is the tell.

    ``now`` is injectable so the branches can be tested without waiting for a
    Monday.
    """
    now = now or now_ist()
    within_hours = within_market_hours(now)

    traded_today = None
    if ltt:
        try:
            traded_today = (datetime.strptime(ltt, LTT_FORMAT).date() == now.date())
        except ValueError:
            traded_today = None

    is_open = bool(within_hours and traded_today)
    if is_open:
        reason = "Market open"
    elif not within_hours:
        reason = f"Market closed — outside 09:15–15:30 IST ({now:%a %d %b %H:%M} IST)"
    else:
        reason = "Market closed — no trades today (likely an NSE holiday)"

    return {"open": is_open, "reason": reason, "last_trade": ltt,
            "as_of": now.strftime("%d %b %Y %H:%M:%S IST")}


def _cache_path(name):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{name}.json"


def _read_cache(name, ttl):
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - blob.get("fetched_at", 0) > ttl:
        return None
    return blob.get("payload")


def _write_cache(name, payload):
    _cache_path(name).write_text(json.dumps(
        {"fetched_at": time.time(), "payload": payload}))


def _retry(fn, attempts=3, pause=0.6):
    """Call ``fn``, retrying transient Breeze failures.

    Breeze intermittently returns a body that is not JSON; the SDK calls
    ``.json()`` on it and raises "Expecting value: line 1 column 1 (char 0)".
    The next attempt normally succeeds, so a bare retry beats surfacing a
    broken row to the user.

    A missing or expired session is not transient, and the cost is charged per
    symbol: the momentum ranking asks for forty of them, so the first snapshot
    after a restart spent 16.9 of its 18.1 seconds asleep in here waiting to fail
    again. Those are raised straight away.
    """
    last = None
    for attempt in range(attempts):
        try:
            return fn()
        except (SessionUnavailable, SessionExpired):
            raise
        except Exception as exc:
            last = exc
            if attempt < attempts - 1 and pause:
                time.sleep(pause)
    raise last


SESSION_EXPIRED_MSG = (
    "Breeze rejected the stored session — it has expired. Breeze sessions last "
    "only the trading day. Enter today's session ID in the Session panel above."
)


def _call(fn):
    """Run a Breeze call, retrying transients and flagging an expired session.

    An expired session comes back as a well-formed response, not an exception,
    so without this check the caller silently renders blank prices.
    """
    resp = _retry(fn)
    if is_session_dead(resp):
        raise SessionExpired(SESSION_EXPIRED_MSG)
    return resp


def _normalise(rows):
    """Coerce Breeze candle strings to floats and drop unusable rows."""
    out = []
    for r in rows or []:
        try:
            out.append({
                "datetime": r.get("datetime"),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                # Breeze intraday data carries trade corrections as negative
                # volume (a real RELIND bar reported -4,865,882). Volume cannot
                # be negative; the close is still good, so keep the bar and
                # zero the volume so it drops out of volume statistics.
                "volume": max(float(r.get("volume") or 0), 0.0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_candles(code, interval, market_open=False):
    """Candles for one symbol/interval, cached to stay inside the rate limit."""
    name = f"candles_{code}_{interval}"
    ttl = config.CANDLE_TTL_OPEN if market_open else config.CANDLE_TTL_CLOSED
    cached = _read_cache(name, ttl)
    if cached is not None:
        return cached

    if interval == "1day":
        start = f"{config.HISTORY_START}T07:00:00.000Z"
    else:
        start = (now_ist() - timedelta(days=config.INTRADAY_DAYS)).strftime(
            "%Y-%m-%dT07:00:00.000Z")
    end = now_ist().strftime("%Y-%m-%dT07:00:00.000Z")

    resp = _call(lambda: client().get_historical_data_v2(
        interval=interval, from_date=start, to_date=end, stock_code=code,
        exchange_code=config.EXCHANGE_CODE, product_type=config.PRODUCT_TYPE))
    candles = _normalise(resp.get("Success"))
    if candles:
        _write_cache(name, candles)
    return candles


def fetch_quote(code, market_open=False):
    """Latest quote for one symbol, NSE row only."""
    name = f"quote_{code}"
    ttl = config.QUOTE_TTL_OPEN if market_open else config.QUOTE_TTL_CLOSED
    cached = _read_cache(name, ttl)
    if cached is not None:
        return cached

    resp = _call(lambda: client().get_quotes(
        stock_code=code, exchange_code=config.EXCHANGE_CODE,
        product_type=config.PRODUCT_TYPE, right="others", strike_price="0"))
    rows = resp.get("Success") or []
    # get_quotes returns NSE *and* BSE; picking the wrong one shows the wrong venue.
    nse = [r for r in rows if r.get("exchange_code") == config.EXCHANGE_CODE]
    quote = nse[0] if nse else (rows[0] if rows else None)
    if quote:
        _write_cache(name, quote)
    return quote


HISTORY_DIR = Path(__file__).parent / "data" / "history"


def _last_date(rows):
    """Date string of the newest bar, or None."""
    for row in reversed(rows or []):
        stamp = row.get("datetime")
        if stamp:
            return stamp[:10]
    return None


def needs_topup(stored, recent):
    """Whether ``recent`` carries bars newer than ``stored``."""
    newest_recent = _last_date(recent)
    if not newest_recent:
        return False
    newest_stored = _last_date(stored)
    return newest_stored is None or newest_recent > newest_stored


_topped_up = set()


def _long_history(code, market_open=False):
    """Cached multi-year daily bars for momentum, topped up when stale.

    The file is written once by history.py, so without a top-up a universe
    symbol would serve a frozen last bar indefinitely while symbols with no
    file picked up new bars normally. Attempted at most once per process per
    symbol, so it costs one extra call per server run and only when behind.
    """
    path = HISTORY_DIR / f"{code}_1day.json"
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    if code not in _topped_up:
        _topped_up.add(code)
        try:
            recent = fetch_candles(code, "1day", market_open)
            if needs_topup(stored, recent):
                from history import merge_candles
                stored = merge_candles([stored, recent])
                path.write_text(json.dumps(stored))
        except Exception:
            pass      # a stale-but-long history beats no history

    # Cleaned then restated, in that order: an NSE special-session bar sitting
    # beside a split date otherwise makes the step comparison read a doubled price
    # and a needed adjustment gets skipped. Both applied on the way out only -- the
    # file above stays as Breeze served it, or each read would compound.
    return splits.adjust_bars(dataquality.clean_bars(stored), splits.for_code(code))


def _stop_feed():
    """Close the tick socket, disconnecting it rather than merely forgetting it.

    ``LiveFeed.stop()`` only calls ``ws_disconnect`` when handed a client. Called
    without one it clears the flags and leaves the connection to Breeze open for
    the rest of the process's life, while ``status()`` reports it closed. Building
    the client can fail when no session or credentials are armed, and in that case
    clearing local state is still right: nothing may keep claiming the feed is up.
    """
    try:
        livefeed.feed.stop(client())
    except Exception:
        livefeed.feed.stop()


MOMENTUM_TTL = 30 * 60

_momentum_cache = {"at": 0.0, "key": None, "value": {}}


def reset_momentum_cache():
    _momentum_cache.update({"at": 0.0, "key": None, "value": {}})


def _compute_momentum_ranking(extra_candles=None):
    """Rank the cached universe (plus the watchlist) by 12-1 momentum.

    Momentum only means something relative to other names, so it is reported as
    a rank across the widest universe available rather than as a bare number.
    """
    from momentum import momentum_score
    from readout import rank_universe

    # Both sources arrive already restated for splits -- _long_history adjusts on
    # the way out, and snapshot adjusts what it passes in. Adjusting again here
    # would halve prices twice and turn a flat name into a 100% gainer.
    scores = {}
    for path in HISTORY_DIR.glob("*_1day.json"):
        code = path.stem.replace("_1day", "")
        rows = _long_history(code)
        if rows:
            scores[code] = momentum_score([r["close"] for r in rows])
    for code, rows in (extra_candles or {}).items():
        if code not in scores or scores.get(code) is None:
            scores[code] = momentum_score([r["close"] for r in rows])
    return rank_universe(scores)


def momentum_ranking(extra_candles=None, compute=None, now=None):
    """Cached momentum ranking.

    Reads ~20 MB of history JSON, so recomputing it per poll was the dominant
    cost of a snapshot. The 12-1 window ends a month ago and new daily bars
    appear only after the close, so the answer changes at most once a day.
    """
    compute = compute or _compute_momentum_ranking
    now = time.time() if now is None else now
    key = tuple(sorted((extra_candles or {}).keys()))

    if _momentum_cache["key"] == key and now - _momentum_cache["at"] < MOMENTUM_TTL:
        return _momentum_cache["value"]

    value = compute(extra_candles)
    _momentum_cache.update({"at": now, "key": key, "value": value})
    return value


def snapshot(timeframe=None):
    """Build the full dashboard payload for the watchlist."""
    from readout import NOT_ADVICE, trend_state
    from watchlist import load as load_watchlist

    timeframe = timeframe if timeframe in config.TIMEFRAMES else config.DEFAULT_TIMEFRAME
    rows, first_ltt, daily_by_code = [], None, {}

    # Clock decides freshness; the quote then refines open/closed for holidays.
    # Previously fetch_quote was called with no flag, so QUOTE_TTL_OPEN never
    # applied and a pre-open quote kept the app on "closed" for 30 minutes.
    hours = within_market_hours()

    # Streaming replaces polling. Ticks arrive on a persistent socket to a
    # different host, so they do not consume the 100/min REST budget, whereas
    # polling 3 symbols every 20s over one session costs ~3,375 of the 5,000
    # daily calls.
    codes = [e["code"] for e in load_watchlist()]
    if hours:
        try:
            # Marks the feed as watched. The background watcher drops the socket
            # once these stop arriving, i.e. once the dashboard is closed.
            livefeed.feed.note_activity()
            livefeed.feed.ensure_started(client(), codes)
        except Exception:
            pass                      # a dead feed must not break the page
    else:
        _stop_feed()

    session_error = None
    for entry in load_watchlist():
        code, label = entry["code"], entry["label"]
        row = {"code": code, "label": label, "timeframe": timeframe}
        if session_error:
            # The session is dead for every symbol; stop spending API calls.
            row["error"] = session_error
            rows.append(row)
            continue
        try:
            tick = livefeed.feed.latest(code)
            live = livefeed.is_fresh(tick)
            # With live ticks the REST quote is only needed occasionally, for
            # the last-trade-time that drives the holiday check.
            quote = fetch_quote(code, hours and not live)
            if first_ltt is None and quote:
                first_ltt = quote.get("ltt")
            is_open = market_state(quote.get("ltt") if quote else None)["open"]
            # Refresh candles at the open-market rate whenever the clock says so.
            fresh = hours or is_open

            # _long_history restates for splits itself; the fallback and the
            # intraday series come straight from Breeze, so they are as-traded
            # and have to be restated here or RSI and the SMA distances read a
            # share subdivision as a crash.
            events = splits.for_code(code)
            daily = _long_history(code, fresh)
            if not daily:
                daily = splits.adjust_bars(
                    dataquality.clean_bars(fetch_candles(code, "1day", fresh)), events)
            daily_by_code[code] = daily
            candles = (daily if timeframe == "1day" else
                       splits.adjust_bars(fetch_candles(code, timeframe, fresh), events))

            row["quote"] = {
                "ltp": quote.get("ltp"), "ltt": quote.get("ltt"),
                "previous_close": quote.get("previous_close"),
                "change_pct": quote.get("ltp_percent_change"),
                "open": quote.get("open"), "high": quote.get("high"),
                "low": quote.get("low"),
                "volume": quote.get("total_quantity_traded"),
                # NOT the exchange's daily circuit limits, despite the names.
                # Measured on two symbols they are exactly ltp x 0.9 and ltp x 1.1,
                # recomputed on every quote, so they move with the price and cannot
                # act as a session ceiling. Passed through for reference only --
                # nothing in the UI presents them as a band.
                "lower_circuit": quote.get("lower_circuit"),
                "upper_circuit": quote.get("upper_circuit"),
            } if quote else None

            if live and row["quote"] is None:
                row["quote"] = {}
            if live:
                row["quote"].update({
                    "ltp": tick["ltp"],
                    "change_pct": tick["change_pct"],
                    "previous_close": tick["previous_close"],
                    "open": tick["open"], "high": tick["high"], "low": tick["low"],
                    "volume": tick["volume"], "ltt": tick["ltt"],
                })
            row["live"] = bool(live)

            sig = evaluate(candles, daily_sessions=len(daily))
            by_name = {v["name"]: v for v in sig["indicators"]}
            row["readout"] = {
                "state": trend_state(by_name["Price vs 20SMA"]["value"],
                                     by_name["Price vs 50SMA"]["value"],
                                     sig["bars"]),
                "data_confidence": sig["confidence"],
                "bars": sig["bars"],
                "indicators": sig["indicators"],
                "caveats": sig["caveats"],
                "not_advice": NOT_ADVICE,
            }
        except SessionExpired as exc:
            session_error = str(exc)
            row["error"] = session_error
        except Exception as exc:  # one bad symbol must not kill the page
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)

    ranking = momentum_ranking(daily_by_code)
    for row in rows:
        if "readout" in row:
            row["readout"]["momentum"] = ranking.get(row["code"])

    from readout import glossary

    return {
        "market": market_state(first_ltt),
        "timeframe": timeframe,
        "timeframes": config.TIMEFRAMES,
        "symbols": rows,
        "glossary": glossary(),
        "session_error": session_error,
        "feed": livefeed.feed.status(),
    }


def current_prices():
    """Latest known price per watchlist symbol, without any API call.

    Live ticks first, then whatever quote is already on disk at any age. The
    alert watcher runs on a timer, so it must never spend REST quota.
    """
    from watchlist import load as load_watchlist

    prices = {}
    for entry in load_watchlist():
        code = entry["code"]
        tick = livefeed.feed.latest(code)
        if livefeed.is_fresh(tick):
            prices[code] = tick["ltp"]
            continue
        cached = _read_cache(f"quote_{code}", float("inf"))
        if cached and cached.get("ltp") is not None:
            prices[code] = cached["ltp"]
    return prices


def health():
    """Session and market status, for the page's banner."""
    try:
        client()
        session_ok, detail = True, "Session loaded from data/session.json"
    except SessionUnavailable as exc:
        session_ok, detail = False, str(exc)
    return {"session_ok": session_ok, "detail": detail,
            "market": market_state(None)}
