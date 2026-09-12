"""HTTP Basic gate in front of the whole app.

``REQUIRE_LOGIN`` and the Google sign-in are enforced **in the browser only**.
They hide the UI; they protect nothing. On a public host every endpoint is
reachable without signing in, including ``POST /api/credentials``, which would
let a stranger overwrite the Breeze App Key and Secret, and ``POST /api/session``,
which would let them install a session. Those keys are order-capable.

So the server checks a credential of its own. Either of two satisfies it:

* ``AUTH_PASSWORD`` -- HTTP Basic, independent of Firebase because verifying an ID
  token needs Google's rotating public keys and a broker gate should not fail open
  because a key fetch timed out.
* ``REQUIRE_FIREBASE_AUTH`` plus ``ALLOWED_EMAILS`` -- a verified ID token whose
  address is on the list. The list is not optional: a token proves only that Google
  issued it for this project, and the Google provider admits *any* Google account.

Disabled when neither is configured, so local use over loopback is unchanged.
"""

import base64
import os
import posixpath
import secrets

USER_VAR = "AUTH_USER"
PASSWORD_VAR = "AUTH_PASSWORD"
DEFAULT_USER = "trader"

# Render pings /api/config to decide whether a deploy is healthy, so it has to
# answer without credentials. It returns local config only -- the Firebase web
# apiKey, which is a public project identifier rather than a secret.
#
# The app shell is open for a harder reason: it is the page that runs the Firebase
# SDK, so gating it under a token-only deployment is a permanent lockout rather
# than a prompt -- no page, no sign-in, no token, and nothing to prompt with once
# AUTH_PASSWORD is gone. It carries no data either; every value it shows arrives
# from a gated /api/ route, and the file is already public on GitHub.
OPEN_PATHS = frozenset({"/", "/api/config"})
OPEN_PREFIXES = ("/static/",)


def open_path(path):
    """Whether this path may be served with no credential at all.

    Normalised first, because ASGI hands over an already-percent-decoded path:
    without this, ``/static/..%2f..%2fapi/session/key`` arrives as
    ``/static/../../api/session/key``, matches the prefix, and walks straight out
    of the shell.
    """
    clean = posixpath.normpath(path or "/")
    return clean in OPEN_PATHS or clean.startswith(OPEN_PREFIXES)


def _clean(env, name):
    return (env.get(name) or "").strip()


def configured(env=None):
    """Whether a password is set, i.e. whether the gate is active at all."""
    return bool(_clean(os.environ if env is None else env, PASSWORD_VAR))


def expected(env=None):
    """The (user, password) pair the request must match."""
    env = os.environ if env is None else env
    return _clean(env, USER_VAR) or DEFAULT_USER, _clean(env, PASSWORD_VAR)


def parse(header):
    """``('user', 'pass')`` from an Authorization header, or None if unusable."""
    if not header or not isinstance(header, str):
        return None
    scheme, _, encoded = header.partition(" ")
    if scheme.strip().lower() != "basic" or not encoded.strip():
        return None
    try:
        raw = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except Exception:
        return None
    if ":" not in raw:
        return None
    user, _, password = raw.partition(":")   # a password may contain colons
    return user, password


FIREBASE_VAR = "REQUIRE_FIREBASE_AUTH"
ALLOWED_VAR = "ALLOWED_EMAILS"


def firebase_required(env=None):
    """Whether a verified Firebase ID token is an accepted credential.

    Off unless explicitly switched on. Deliberately *not* inferred from the mere
    presence of firebase-web-config.json: that file exists on a development machine,
    and inferring from it would start demanding tokens on loopback the moment token
    support shipped.
    """
    env = os.environ if env is None else env
    return _clean(env, FIREBASE_VAR).lower() in ("true", "1", "yes", "on")


def allowed_accounts(env=None):
    """The Google addresses a verified token may belong to, lowercased.

    A valid token proves Google issued it for this project -- not that its owner is
    welcome. A Firebase project with the Google provider enabled accepts *any*
    Google account, so "signed in" and "authorised" are different questions and
    only this list answers the second.

    Unset means nobody, never everybody: an empty list has to fail closed, or
    forgetting it would be indistinguishable from opening the app to the internet.
    """
    env = os.environ if env is None else env
    raw = _clean(env, ALLOWED_VAR).replace(";", ",").replace(" ", ",")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def active(env=None, project_id=None):
    """Whether any gate is switched on at all."""
    return bool(configured(env)
                or (firebase_required(env) and (project_id or "").strip()))


def gate_warning(env=None, project_id=None):
    """One line about a gate that is not doing what its settings imply, or None.

    Printed at startup because neither of these is visible from the app: an empty
    allowlist refuses every sign-in and reads as a broken deploy, while no gate at
    all reads as a working one.
    """
    env = os.environ if env is None else env
    if not active(env, project_id):
        return ("No gate is active: every endpoint is public, including "
                "POST /api/credentials, which overwrites the Breeze keys. Set "
                "AUTH_PASSWORD, or REQUIRE_FIREBASE_AUTH with ALLOWED_EMAILS.")
    if firebase_required(env) and not (project_id or "").strip():
        return (f"{FIREBASE_VAR} is on but Firebase is not configured, so no token "
                "can be verified and only the password will be accepted.")
    if firebase_required(env) and not allowed_accounts(env):
        return (f"{FIREBASE_VAR} is on but {ALLOWED_VAR} is empty, so no Google "
                "account is authorised. Add the addresses that may sign in.")
    return None


def _basic_allows(header, env):
    got = parse(header)
    if got is None:
        return False
    want_user, want_password = expected(env)
    # Compared as UTF-8 bytes: compare_digest raises TypeError on a non-ASCII str,
    # which turned `Authorization: Basic <base64 of "usér:pass">` into an
    # unauthenticated HTTP 500 from inside the gate. Bytes also let a non-ASCII
    # password actually work rather than merely fail safely.
    #
    # Both halves are always compared, in constant time, so neither leaks by timing.
    user_ok = secrets.compare_digest(got[0].encode("utf-8"),
                                    want_user.encode("utf-8"))
    password_ok = secrets.compare_digest(got[1].encode("utf-8"),
                                        want_password.encode("utf-8"))
    return user_ok and password_ok


def _token_allows(header, project_id, verifier, env):
    raw = (header or "").strip()
    if not raw.lower().startswith("bearer"):
        return False                  # a Basic header can never be a token
    allowed = allowed_accounts(env)
    if not allowed:
        return False                  # nobody listed, so nobody is authorised
    try:
        if verifier is None:
            import firebase_auth
            verifier = firebase_auth.verify
        claims = verifier(raw, project_id)
    except Exception:
        return False                  # unavailable means denied, not allowed
    if not claims or not claims.get("email_verified"):
        # An unverified address is a claim, not a fact, so it cannot be matched
        # against the list. Google sign-in always reports a verified one.
        return False
    return str(claims.get("email") or "").strip().lower() in allowed


def permits(path, header, env=None, project_id=None, verifier=None):
    """Whether this request may proceed.

    Either credential satisfies an active gate, so a Bearer token can be introduced
    without invalidating the password an existing deployment is already using.
    """
    if open_path(path) or not active(env, project_id):
        return True
    if configured(env) and _basic_allows(header, env):
        return True
    if firebase_required(env) and (project_id or "").strip():
        return _token_allows(header, project_id, verifier, env)
    return False
