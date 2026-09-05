"""HTTP Basic gate in front of the whole app.

``REQUIRE_LOGIN`` and the Google sign-in are enforced **in the browser only**.
They hide the UI; they protect nothing. On a public host every endpoint is
reachable without signing in, including ``POST /api/credentials``, which would
let a stranger overwrite the Breeze App Key and Secret, and ``POST /api/session``,
which would let them install a session. Those keys are order-capable.

So the server checks a password of its own. It is deliberately independent of
Firebase: verifying an ID token needs Google's rotating public keys, and a broker
gate should not fail open because a key fetch timed out.

Disabled when no password is configured, so local use over loopback is unchanged.
"""

import base64
import os
import secrets

USER_VAR = "AUTH_USER"
PASSWORD_VAR = "AUTH_PASSWORD"
DEFAULT_USER = "trader"

# Render pings this to decide whether a deploy is healthy, so it has to answer
# without credentials. It returns local config only -- the Firebase web apiKey,
# which is a public project identifier rather than a secret.
OPEN_PATHS = frozenset({"/api/config"})


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


def permits(path, header, env=None):
    """Whether this request may proceed."""
    if path in OPEN_PATHS or not configured(env):
        return True
    got = parse(header)
    if got is None:
        return False
    want_user, want_password = expected(env)
    # Both halves compared in constant time, so neither leaks by timing.
    return (secrets.compare_digest(got[0], want_user)
            and secrets.compare_digest(got[1], want_password))
