"""Verify Firebase ID tokens on the server.

``REQUIRE_LOGIN`` and the Google sign-in button run in the browser: they hide the UI
and protect nothing, which is why ``auth.py`` carries a separate HTTP Basic gate.
Anything per-user is impossible without the check here, because a browser can claim
any uid it likes -- the server has to prove who is calling before it can key
credentials, sessions or watchlists by user.

Verified locally against Google's published signing keys rather than by calling an
API per request: no added latency, and no dependency on Google being reachable while
serving. The keys rotate roughly daily, so they are cached and refetched once on an
unknown ``kid`` -- without that refetch, every user is locked out for the rest of the
cache window each time Google rotates.

Every failure path returns None. There is no branch that lets an unverified token
through, including a key-fetch failure: unavailable means denied, not allowed.
"""

import json
import threading
import time
import urllib.request

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.x509 import load_pem_x509_certificate

# Google's public certificates for Firebase ID tokens.
CERT_URL = ("https://www.googleapis.com/robot/v1/metadata/x509/"
            "securetoken@system.gserviceaccount.com")
KEY_TTL = 3600.0
FETCH_TIMEOUT = 10

ISSUER_PREFIX = "https://securetoken.google.com/"

# Sentinel so a test can delete a claim rather than set it to None.
_REMOVE = object()

_lock = threading.Lock()
_cache = {"keys": None, "at": 0.0}


def reset_key_cache():
    with _lock:
        _cache.update(keys=None, at=0.0)


def _fetch_google_keys():
    """kid -> public key, from Google's x509 endpoint."""
    request = urllib.request.Request(CERT_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as resp:
        certs = json.loads(resp.read().decode("utf-8"))
    out = {}
    for kid, pem in (certs or {}).items():
        try:
            out[kid] = load_pem_x509_certificate(pem.encode("utf-8")).public_key()
        except Exception:
            try:
                out[kid] = load_pem_public_key(pem.encode("utf-8"))
            except Exception:
                continue
    return out


def _keys(fetcher, now, force=False):
    with _lock:
        fresh = (_cache["keys"] is not None
                 and now - _cache["at"] < KEY_TTL and not force)
        if fresh:
            return _cache["keys"]
    keys = fetcher()                       # outside the lock: this does network I/O
    with _lock:
        _cache.update(keys=keys, at=now)
    return keys


def enabled(project_id=None):
    """Whether token verification can run at all. No project id, no check."""
    return bool((project_id or "").strip())


def _strip_bearer(raw):
    text = (raw or "").strip()
    if text.lower().startswith("bearer"):
        text = text[6:].strip()
    return text


def verify(token, project_id, keys=None, now=None):
    """Claims dict with ``uid`` for a valid token, else None. Never raises.

    ``aud`` and ``iss`` are checked against ``project_id`` because anyone can create
    their own Firebase project and sign perfectly valid tokens in it -- the signature
    alone proves only that *Google* issued it, not that it was issued for us.
    """
    project = (project_id or "").strip()
    raw = _strip_bearer(token)
    if not project or not raw or raw.count(".") != 2:
        return None
    now = time.time() if now is None else now
    fetcher = keys or _fetch_google_keys

    try:
        kid = jwt.get_unverified_header(raw).get("kid")
    except Exception:
        return None
    if not kid:
        return None

    try:
        available = _keys(fetcher, now) or {}
        key = available.get(kid)
        if key is None:
            # Probably a rotation rather than an attack; try once with fresh keys.
            key = (_keys(fetcher, now, force=True) or {}).get(kid)
        if key is None:
            return None

        claims = jwt.decode(
            raw, key=key, algorithms=["RS256"], audience=project,
            issuer=ISSUER_PREFIX + project,
            options={"require": ["exp", "iat", "sub", "aud", "iss"],
                     "verify_signature": True, "verify_exp": True,
                     "verify_aud": True, "verify_iss": True},
        )
    except Exception:
        return None

    uid = str(claims.get("sub") or "").strip()
    if not uid:
        return None
    return {"uid": uid, "email": claims.get("email"),
            "email_verified": bool(claims.get("email_verified")),
            "exp": claims.get("exp")}
