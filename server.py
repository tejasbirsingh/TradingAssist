"""Local dashboard server.

The browser polls this server, never Breeze directly. That keeps UI refresh
rate decoupled from Breeze's 100/min and 5000/day quota.

Run:
    .venv/bin/uvicorn server:app --reload --port 8000
"""

import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import alerts
import app_config
import auth
import breeze_client
import credentials
import firebase_config
import ipo
import ipo_reminders
import livefeed
import market_data
import messaging
import notify
import watchlist
from breeze_client import SessionUnavailable

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

ALERT_POLL_SECONDS = 5
_watcher_stop = threading.Event()
# The watcher must not crash the server, but a swallowed exception once hid a
# NameError here, so the last failure is recorded and surfaced via /api/alerts.
_watcher_error = {"detail": None, "at": None}


def _reap_idle_feed():
    """Close the tick socket when nobody has loaded the dashboard for a while.

    Lives on the watcher rather than the request path because the whole point is
    to notice an *absence* of requests. Failures are non-fatal: an unclosed
    socket is a wasted connection, not a broken dashboard.
    """
    try:
        if livefeed.feed.should_idle():
            livefeed.feed.reap_if_idle(market_data.client())
    except Exception:
        livefeed.feed.reap_if_idle()      # drop it locally even if Breeze is gone


IPO_CHECK_SECONDS = 60 * 60
_last_ipo_check = {"at": 0.0}
# Last few delivery outcomes, surfaced by GET /api/ipo-reminders so a channel that
# has quietly stopped working is visible before a deadline depends on it.
_last_delivery = []


def _check_ipo_reminders(now=None):
    """Send any IPO reminder that has come due. Hourly, not every 5 seconds.

    An IPO window moves in days, and ipo.load() hits NSE, so polling it at the
    alert cadence would be both pointless and rude to a third party.

    Goes out on the desktop *and* on whatever off-device channel is configured. The
    off-device one is what matters: a deadline reminder is no use if the laptop is
    shut. Delivery outcomes are recorded rather than discarded -- at a handful of
    messages a month, a revoked app password would otherwise go unnoticed until the
    day it mattered.
    """
    now = time.time() if now is None else now
    if now - _last_ipo_check["at"] < IPO_CHECK_SECONDS:
        return []
    _last_ipo_check["at"] = now

    stored = ipo_reminders.load()
    if not stored:
        return []
    listed = ipo.load().get("ipos") or []
    due = ipo_reminders.due(stored, listed)
    for item in due:
        notify.send(item["subject"], item["body"])
        for result in messaging.dispatch(item["subject"], item["body"]):
            _last_delivery.append({**result, "symbol": item["symbol"],
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    del _last_delivery[:-10]
    if due:
        ipo_reminders.save(ipo_reminders.mark_sent(stored, due))
    return due


def _watch_alerts():
    """Fire alerts from live ticks, independent of any open browser tab.

    Reads only the tick feed and on-disk quote cache, so it costs no REST quota.
    """
    while not _watcher_stop.is_set():
        try:
            fired, updated = alerts.evaluate(alerts.load(), market_data.current_prices())
            if fired:
                alerts.save(updated)
                for hit in fired:
                    notify.send(notify.TITLE,
                                alerts.describe(hit, hit["triggered_price"]))
            _reap_idle_feed()
            _check_ipo_reminders()
            _watcher_error.update(detail=None, at=None)
        except Exception as exc:
            _watcher_error.update(detail=f"{type(exc).__name__}: {exc}",
                                  at=time.strftime("%H:%M:%S"))
        _watcher_stop.wait(ALERT_POLL_SECONDS)


@asynccontextmanager
async def lifespan(app):
    # Printed rather than raised: a misconfigured gate must not stop the service,
    # but it must not be silent either. On Render this is the deploy log.
    warning = auth.gate_warning(project_id=(firebase_config.load() or {}).get("projectId"))
    if warning:
        print(f"AUTH WARNING: {warning}", flush=True)
    thread = threading.Thread(target=_watch_alerts, daemon=True)
    thread.start()
    yield
    _watcher_stop.set()


app = FastAPI(title="Trading Assist", docs_url="/api/docs", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def require_password(request, call_next):
    """Refuse everything unauthenticated once AUTH_PASSWORD is set.

    Registered as middleware rather than a per-route dependency so a route added
    later is covered by default. Inactive with no password configured, which
    leaves loopback use unchanged.
    """
    config = firebase_config.load() or {}
    if not auth.permits(request.url.path, request.headers.get("authorization"),
                        project_id=config.get("projectId")):
        # Only offer the browser's native password dialog when a password is what
        # would actually satisfy the gate. Sending it under a token-only gate would
        # prompt for credentials that cannot work.
        headers = ({"WWW-Authenticate": 'Basic realm="Trading Assist"'}
                   if auth.configured() else {})
        return JSONResponse(status_code=401, headers=headers,
                            content={"detail": "Authentication required."})
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def api_health():
    return market_data.health()


@app.get("/api/config")
def api_config():
    """Client config. Firebase is optional; absent means the feature is off."""
    return firebase_config.status()


@app.get("/api/credentials")
def api_credentials_status():
    """Whether credentials are armed and where from. Never the values."""
    return credentials.status()


@app.post("/api/credentials")
def api_credentials_set(api_key: str = Body(..., embed=True),
                        secret_key: str = Body(..., embed=True)):
    """Arm this process with a user's credentials, in memory only.

    Nothing is written to disk: the browser's localStorage is the only durable
    copy, so the server never holds anyone's order-capable broker keys.
    """
    try:
        status = credentials.set_credentials(api_key, secret_key)
    except credentials.CredentialsMissing as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    market_data.reset_client()
    return status


@app.delete("/api/credentials")
def api_credentials_clear():
    credentials.clear()
    market_data.reset_client()
    return credentials.status()


@app.get("/api/session")
def api_session_status():
    """Session status. The login URL is built in the browser, which already has
    the app key, so this response never carries it."""
    info = breeze_client.session_info()
    info["credentials"] = credentials.status()
    return info


@app.get("/api/session/key")
def api_session_export():
    """The stored session including its key, for the browser to keep.

    Hosts with an ephemeral disk lose data/session.json on every restart, so the
    browser is the durable copy — as it already is for the App Key and Secret,
    which are strictly more powerful since they can mint new session keys.
    """
    saved = breeze_client.exportable_session()
    if saved is None:
        return JSONResponse(status_code=404,
                            content={"detail": "No session is stored."})
    return saved


@app.post("/api/session/restore")
def api_session_restore(user_id: str = Body(...), session_key: str = Body(...),
                        app_key_fp: str = Body(default=None)):
    """Reinstate a session the browser was holding, after the server lost it."""
    try:
        info = breeze_client.restore_session(user_id, session_key, app_key_fp)
    except SessionUnavailable as exc:
        return JSONResponse(status_code=400,
                            content={"ok": False, "detail": str(exc)})
    market_data.reset_client()
    return info


@app.post("/api/session")
def api_session_submit(session_token: str = Body(..., embed=True)):
    """Exchange a freshly copied apisession and store the resulting key.

    Exchanged synchronously here because the raw token is one-shot and goes
    stale within minutes.
    """
    try:
        info = breeze_client.exchange_and_store(session_token)
    except SessionUnavailable as exc:
        return JSONResponse(status_code=400,
                            content={"ok": False, "detail": str(exc)})
    market_data.reset_client()      # next request uses the new session key
    return info


@app.get("/api/ipos")
def api_ipos():
    """Open and upcoming IPOs. Sourced from NSE, not Breeze, which has no IPO API."""
    return ipo.load()


@app.get("/api/ipo-reminders")
def api_ipo_reminders():
    return {"reminders": ipo_reminders.load(),
            "channels": messaging.configured(),
            "deliveries": _last_delivery,
            "watcher_error": _watcher_error["detail"]}


@app.post("/api/ipo-reminders")
def api_ipo_reminder_add(symbol: str = Body(...), days_before: int = Body(default=1)):
    """Track one issue's deadline. The issue is looked up in the live feed so the
    dates come from NSE rather than from whatever the browser happened to show."""
    wanted = (symbol or "").strip().upper()
    listed = {str(i.get("symbol") or "").strip().upper(): i
              for i in (ipo.load().get("ipos") or [])}
    if wanted not in listed:
        return JSONResponse(status_code=404, content={
            "detail": f"{wanted} is not in NSE's current issue list."})
    try:
        entries = ipo_reminders.add(ipo_reminders.load(), listed[wanted],
                                   days_before=days_before)
    except ipo_reminders.ReminderError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    ipo_reminders.save(entries)
    return {"reminders": entries}


@app.delete("/api/ipo-reminders/{symbol}")
def api_ipo_reminder_remove(symbol: str):
    try:
        entries = ipo_reminders.remove(ipo_reminders.load(), symbol)
    except ipo_reminders.ReminderError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    ipo_reminders.save(entries)
    return {"reminders": entries}


@app.get("/api/symbols")
def api_symbol_search(q: str = Query(default="", min_length=0)):
    """Look up NSE equities by company name or code, for the add-stock box."""
    try:
        return {"results": watchlist.search(q)}
    except Exception as exc:
        return JSONResponse(status_code=503,
                            content={"detail": f"Security master unavailable: {exc}"})


@app.get("/api/watchlist")
def api_watchlist():
    return {"watchlist": watchlist.load()}


@app.post("/api/watchlist")
def api_watchlist_add(code: str = Body(..., embed=True)):
    try:
        entries = watchlist.add(code, watchlist.load())
    except watchlist.WatchlistError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    watchlist.save(entries)
    return {"watchlist": entries}


@app.put("/api/watchlist")
def api_watchlist_replace(codes: list[str] = Body(..., embed=True)):
    """Replace the whole watchlist, for syncing a list down from another device.

    Invalid codes are reported rather than rejecting the sync, so one stale
    symbol cannot discard the rest of the list.
    """
    entries, rejected = watchlist.replace(codes)
    watchlist.save(entries)
    return {"watchlist": entries, "rejected": rejected}


@app.delete("/api/watchlist/{code}")
def api_watchlist_remove(code: str):
    try:
        entries = watchlist.remove(code, watchlist.load())
    except watchlist.WatchlistError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    watchlist.save(entries)
    return {"watchlist": entries}


@app.get("/api/alerts")
def api_alerts():
    return {"alerts": alerts.load(), "notifications": notify.available(),
            "watcher_error": _watcher_error["detail"],
            "watcher_error_at": _watcher_error["at"]}


@app.post("/api/alerts")
def api_alerts_add(code: str = Body(...), direction: str = Body(...),
                   price: float = Body(...), note: str = Body(default="")):
    try:
        entries = alerts.add(alerts.load(), code, direction, price, note)
    except alerts.AlertError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    alerts.save(entries)
    return {"alerts": entries}


@app.delete("/api/alerts/{alert_id}")
def api_alerts_remove(alert_id: str):
    try:
        entries = alerts.remove(alerts.load(), alert_id)
    except alerts.AlertError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    alerts.save(entries)
    return {"alerts": entries}


@app.post("/api/alerts/{alert_id}/rearm")
def api_alerts_rearm(alert_id: str):
    try:
        entries = alerts.rearm(alerts.load(), alert_id)
    except alerts.AlertError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    alerts.save(entries)
    return {"alerts": entries}


@app.get("/api/snapshot")
def api_snapshot(timeframe: str = Query(default=app_config.DEFAULT_TIMEFRAME)):
    """Watchlist signals. 503 with remediation text when the session is dead."""
    try:
        return market_data.snapshot(timeframe)
    except SessionUnavailable as exc:
        return JSONResponse(
            status_code=503,
            content={"error": "session_unavailable", "detail": str(exc),
                     "remediation": ".venv/bin/python refresh_session.py"},
        )
