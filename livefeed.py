"""Live tick feed over Breeze's socket.io stream.

Replaces polling ``get_quotes``. That matters for quota: the REST limit is 100
calls/minute and 5000/day, and polling three symbols every 20 seconds across one
6.25-hour session costs roughly 3,375 calls -- about two thirds of the daily
budget. Ticks arrive on a persistent connection to a different host
(``livestream.icicidirect.com``) and are pushed rather than requested, so they do
not consume that budget.

Observed behaviour that shaped this design:

* An immediate reconnect after a disconnect fails intermittently (roughly one in
  three), so connecting retries with a delay instead of giving up.
* Ticks identify their instrument as ``4.1!<token>``, not by stock code, so the
  token is mapped back through the SDK's own token dictionary.
* Connecting can block for seconds, so it happens on a background thread and
  never inside a request.
"""

import threading
import time

FRESH_SECONDS = 60          # a tick older than this is treated as absent
CONNECT_RETRIES = 4
RETRY_PAUSE = 6.0

# Nobody polls the server while the dashboard is closed, so silence on the
# request path means nobody is watching and the socket can be dropped. Comfortably
# longer than the page's 3-second poll, so a brief network stall does not tear a
# working feed down.
IDLE_AFTER = 120.0


def token_from_symbol(symbol):
    """``4.1!764553`` -> ``764553``. None if it is not that shape."""
    if not symbol or not isinstance(symbol, str) or "!" not in symbol:
        return None
    token = symbol.split("!", 1)[1].strip()
    return token or None


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise_tick(raw, now=None):
    """Reduce a raw tick to the fields the dashboard shows. None if unusable."""
    if not isinstance(raw, dict):
        return None
    ltp = _num(raw.get("last"))
    if ltp is None:
        return None
    volume = _num(raw.get("ttq"))
    return {
        "ltp": ltp,
        "previous_close": _num(raw.get("close")),
        "change_pct": _num(raw.get("change")),
        "open": _num(raw.get("open")),
        "high": _num(raw.get("high")),
        "low": _num(raw.get("low")),
        "volume": int(volume) if volume is not None else None,
        "ltt": raw.get("ltt"),
        "received_at": time.time() if now is None else now,
    }


def is_fresh(tick, now=None, max_age=FRESH_SECONDS):
    """Whether a stored tick is recent enough to display."""
    if not tick or not tick.get("received_at"):
        return False
    return ((time.time() if now is None else now) - tick["received_at"]) <= max_age


class LiveFeed:
    """Holds the newest tick per stock code. The socket writes; requests read."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ticks = {}
        self._token_to_code = {}
        self._subscribed = set()
        self._connected = False
        self._starting = False
        self._error = None
        self._ticks_seen = 0
        self._unmapped = 0
        self._last_tick_at = None
        self._last_used = None

    # ---- socket side ----------------------------------------------------
    def handle_tick(self, raw):
        """Socket callback. Must never raise, or it kills the socket thread."""
        try:
            token = token_from_symbol((raw or {}).get("symbol"))
            tick = normalise_tick(raw)
            if token is None or tick is None:
                return
            with self._lock:
                code = self._token_to_code.get(token)
                if code is None:
                    self._unmapped += 1
                    return
                self._ticks[code] = tick
                self._ticks_seen += 1
                self._last_tick_at = tick["received_at"]
        except Exception:
            pass

    def _map_tokens(self, client, codes):
        """Learn token -> code from the SDK's own security dictionary."""
        found = {}
        for table in getattr(client, "token_script_dict_list", None) or []:
            if not isinstance(table, dict):
                continue
            for token, entry in table.items():
                if entry and entry[0] in codes:
                    found[str(token)] = entry[0]
        with self._lock:
            self._token_to_code.update(found)
        return found

    def _connect_and_subscribe(self, client, codes):
        try:
            client.on_ticks = self.handle_tick
            last_error = None
            for attempt in range(CONNECT_RETRIES):
                try:
                    client.ws_connect()
                    last_error = None
                    break
                except Exception as exc:
                    # Immediate reconnects fail intermittently; wait and retry.
                    last_error = exc
                    time.sleep(RETRY_PAUSE)
            if last_error is not None:
                raise last_error

            self._map_tokens(client, set(codes))
            for code in codes:
                client.subscribe_feeds(
                    exchange_code="NSE", stock_code=code, product_type="cash",
                    get_exchange_quotes=True, get_market_depth=False)
                with self._lock:
                    self._subscribed.add(code)
            with self._lock:
                self._connected = True
                self._error = None
        except Exception as exc:
            with self._lock:
                self._connected = False
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._starting = False

    def ensure_started(self, client, codes):
        """Connect and subscribe in the background. Safe to call every request."""
        codes = [c for c in codes if c]
        with self._lock:
            if self._starting:
                return
            missing = [c for c in codes if c not in self._subscribed]
            if self._connected and not missing:
                return
            self._starting = True
        threading.Thread(target=self._connect_and_subscribe,
                         args=(client, codes), daemon=True).start()

    def stop(self, client=None):
        """Close the socket. ``client`` is required to actually disconnect it.

        The SDK's ``ws_disconnect()`` does not close anything: it calls the
        socket.io *event handler* ``on_disconnect()`` and then sets its own
        reference to None, leaving the underlying ``socketio.Client`` running with
        its default ``reconnection=True``. Measured: after ws_disconnect the
        process still held an ESTABLISHED connection to the stream host, on a new
        source port. So the real socket is closed first, while the handle to it
        still exists, and the SDK call follows only for its own bookkeeping.
        """
        with self._lock:
            was = self._connected
            self._connected = False
            self._subscribed.clear()
        if not (was and client is not None):
            return
        try:
            handler = getattr(client, "sio_rate_refresh_handler", None)
            sio = getattr(handler, "sio", None)
            if sio is not None:
                sio.disconnect()          # explicit, so it does not reconnect
        except Exception:
            pass
        try:
            client.ws_disconnect()
        except Exception:
            pass

    # ---- idle shutdown ---------------------------------------------------
    def note_activity(self, now=None):
        """Record that something asked for live data. Called on every request."""
        with self._lock:
            self._last_used = time.time() if now is None else now

    def idle_seconds(self, now=None):
        """Seconds since the last request, or None if none has arrived yet."""
        with self._lock:
            if self._last_used is None:
                return None
            return (time.time() if now is None else now) - self._last_used

    def should_idle(self, now=None, max_idle=IDLE_AFTER):
        """Whether the socket is open with nobody watching it."""
        with self._lock:
            if not self._connected or self._last_used is None:
                return False
            elapsed = (time.time() if now is None else now) - self._last_used
        return elapsed > max_idle

    def reap_if_idle(self, client=None, now=None, max_idle=IDLE_AFTER):
        """Drop an unwatched socket. True if it was closed by this call.

        Runs on the background watcher, because when the dashboard is closed no
        request arrives to notice the silence.
        """
        if not self.should_idle(now=now, max_idle=max_idle):
            return False
        self.stop(client)
        return True

    # ---- request side ---------------------------------------------------
    def latest(self, code):
        with self._lock:
            return self._ticks.get(code)

    def status(self):
        with self._lock:
            return {
                "connected": self._connected,
                "starting": self._starting,
                "subscribed": sorted(self._subscribed),
                "ticks": self._ticks_seen,
                "unmapped": self._unmapped,
                "last_tick_at": self._last_tick_at,
                "idle_seconds": (None if self._last_used is None
                                 else time.time() - self._last_used),
                "error": self._error,
            }


feed = LiveFeed()
