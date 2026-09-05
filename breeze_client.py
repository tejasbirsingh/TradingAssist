"""Session handling for Breeze.

The raw ``apisession`` in the login-redirect URL is a one-shot value: it is
exchanged at ``customerdetails`` for a ``session_key`` that signs every later
request. Re-exchanging a spent apisession returns ``Status 500 'Request Object
is Null'`` -- a misleading message that looks like a bad request rather than a
spent token.

So the session is exchanged exactly once -- from the dashboard's session form or
``refresh_session.py``, both of which write through ``store_session`` -- and the
resulting key is reused from disk thereafter.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import credentials
from breeze_connect import BreezeConnect
from breeze_connect.breeze_connect import ApificationBreeze

BASE_DIR = Path(__file__).parent
SESSION_PATH = BASE_DIR / "data" / "session.json"

LOGIN_URL_BASE = "https://api.icicidirect.com/apiuser/login?api_key="

STALE_HINT = (
    "Breeze rejected that token. It is one-shot and goes stale within minutes, "
    "so it has most likely been spent already or sat too long. Open the login "
    "link again and submit the new apisession straight away."
)


class SessionUnavailable(RuntimeError):
    """No usable session on disk. Carries operator-facing remediation text."""


class SessionExpired(RuntimeError):
    """A stored session exists but Breeze no longer accepts it."""


# Breeze reports daily expiry as HTTP-200 with Status 500 and one of these,
# so a stored session file is no proof the session still works.
SESSION_DEAD_MARKERS = (
    "invalid user details",
    "invalid session",
    "resource not available",
    "public key does not exist",
)


def is_session_dead(response):
    """True when a Breeze response says the session is no longer valid."""
    if not isinstance(response, dict):
        return False
    error = str(response.get("Error") or "").lower()
    if not error:
        return False
    return any(marker in error for marker in SESSION_DEAD_MARKERS)


def _credentials():
    """Current app credentials, from the browser or a .env fallback."""
    try:
        return credentials.get_credentials()
    except credentials.CredentialsMissing as exc:
        raise SessionUnavailable(str(exc)) from exc


def load_client():
    """Rebuild a signed Breeze client from the persisted session key.

    Deliberately does NOT call ``generate_session``: that would try to
    re-exchange an already-spent apisession and fail.
    """
    api_key, secret_key = _credentials()

    if not SESSION_PATH.exists():
        raise SessionUnavailable(
            "No saved session. Run:  .venv/bin/python refresh_session.py"
        )

    saved = json.loads(SESSION_PATH.read_text())
    user_id, session_key = saved.get("user_id"), saved.get("session_key")
    if not user_id or not session_key:
        raise SessionUnavailable(
            f"{SESSION_PATH} is malformed. Run:  "
            ".venv/bin/python refresh_session.py"
        )

    # Reproduce generate_session() minus the api_util() exchange step.
    breeze = BreezeConnect(api_key=api_key)
    breeze.user_id = user_id
    breeze.session_key = session_key
    breeze.secret_key = secret_key
    breeze.get_stock_script_list()
    breeze.api_handler = ApificationBreeze(breeze)
    return breeze


def session_age_note():
    """Human-readable note about when the session was minted, or None."""
    return session_info().get("saved_at")


def login_url():
    """Breeze login URL for this app key.

    The key is URL-encoded because Breeze keys routinely contain ``+``, ``/``
    and ``=``, which would otherwise terminate or corrupt the query string.
    """
    api_key, _ = _credentials()
    return LOGIN_URL_BASE + quote_plus(api_key)


def store_session(user_id, session_key, saved_epoch=None):
    """Single writer for the session file, so no two paths can disagree."""
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    epoch = time.time() if saved_epoch is None else saved_epoch
    SESSION_PATH.write_text(json.dumps({
        "user_id": user_id,
        "session_key": session_key,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch)),
        "saved_epoch": epoch,
        # Which app key minted this, so swapping credentials cannot silently
        # show another account's data.
        "app_key_fp": credentials.status()["fingerprint"],
    }, indent=2))


def session_info(now=None):
    """Status of the stored session. Never includes the session key itself."""
    blank = {"ok": False, "user_id": None, "saved_at": None,
             "age_minutes": None, "app_key_fp": None, "reason": None}
    if not SESSION_PATH.exists():
        return blank
    try:
        saved = json.loads(SESSION_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return blank
    if not saved.get("user_id") or not saved.get("session_key"):
        return blank

    epoch = saved.get("saved_epoch")
    if not epoch:
        # Files written before saved_epoch existed still carry an ISO saved_at.
        try:
            epoch = datetime.strptime(saved.get("saved_at", ""),
                                      "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except (TypeError, ValueError):
            epoch = None

    age = None
    if epoch:
        age = ((time.time() if now is None else now) - epoch) / 60.0
    stored_fp = saved.get("app_key_fp")
    current_fp = credentials.status()["fingerprint"]
    # A missing fingerprint means a file written before this existed; honour it.
    foreign = bool(stored_fp and current_fp and stored_fp != current_fp)

    return {
        "ok": not foreign,
        "user_id": saved.get("user_id"),
        "saved_at": saved.get("saved_at"),
        "age_minutes": age,
        "app_key_fp": stored_fp,
        "reason": ("This saved session belongs to a different App Key. "
                   "Enter a session ID for the credentials now in use."
                   if foreign else None),
    }


def exportable_session():
    """The stored session, key included, for the browser to keep. None if absent.

    This is the one place the session key leaves the server. It is safe in the
    same sense the credentials already are: the browser is the durable store, and
    the App Key and Secret it already holds are *more* powerful than a session key
    -- they can mint new ones. On a host with an ephemeral disk this is what makes
    a cold start survivable without re-pasting the daily token.
    """
    if not SESSION_PATH.exists():
        return None
    try:
        saved = json.loads(SESSION_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not saved.get("user_id") or not saved.get("session_key"):
        return None
    return {"user_id": saved["user_id"], "session_key": saved["session_key"],
            "app_key_fp": saved.get("app_key_fp")}


def restore_session(user_id, session_key, app_key_fp=None):
    """Write back a session the browser was holding. Returns session_info().

    Validates before touching the file so a rejected restore cannot destroy a
    working session.
    """
    user_id = (user_id or "").strip()
    session_key = (session_key or "").strip()
    if not user_id or not session_key:
        raise SessionUnavailable(
            "A stored session needs both a user id and a session key.")

    current_fp = credentials.status()["fingerprint"]
    if app_key_fp and current_fp and app_key_fp != current_fp:
        raise SessionUnavailable(
            "That stored session was minted by a different App key. Paste a "
            "fresh session ID for the credentials now in use.")

    store_session(user_id, session_key)
    return session_info()


def exchange_and_store(raw_token, client_factory=BreezeConnect):
    """Exchange a fresh apisession and persist the resulting session key.

    Exchanged immediately on submission: the raw token is one-shot and short
    lived, so any delay between the user copying it and this call is risk.
    A failed exchange leaves an existing good session untouched.
    """
    token = (raw_token or "").strip()
    if not token:
        raise SessionUnavailable("Session token is empty.")

    api_key, secret_key = _credentials()
    breeze = client_factory(api_key=api_key)
    try:
        breeze.generate_session(api_secret=secret_key, session_token=token)
    except Exception as exc:
        raise SessionUnavailable(f"{STALE_HINT} (Breeze said: {exc})") from exc

    if not breeze.user_id or not breeze.session_key:
        raise SessionUnavailable(STALE_HINT)

    store_session(breeze.user_id, breeze.session_key)
    return session_info()
