"""Breeze app credentials, supplied per user by the browser.

The api_key and secret_key are held **in memory only** for the life of the
process. The browser's localStorage is the only durable copy, so running this
app never puts anyone's broker credentials on the server's disk -- which matters
because those keys are order-capable.

There is deliberately **no file fallback**. A ``.env`` pair used to be honoured,
but on a deployed instance that meant requests could be served with whichever
keys happened to sit on that host instead of the ones the visitor supplied. The
page re-arms the process from localStorage on load, so a restarted server picks
the credentials back up without anyone retyping them.
"""

import hashlib

_api_key = None
_secret_key = None


class CredentialsMissing(RuntimeError):
    """No usable api_key/secret_key, with operator-facing remediation text."""


def fingerprint(api_key):
    """Short, non-reversible tag for an app key, safe to store and display."""
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def set_credentials(api_key, secret_key):
    """Arm the process with credentials. Never persisted."""
    global _api_key, _secret_key
    api_key = (api_key or "").strip()
    secret_key = (secret_key or "").strip()
    if not api_key or not secret_key:
        raise CredentialsMissing("Both the App Key and the Secret Key are required.")
    _api_key, _secret_key = api_key, secret_key
    return status()


def clear():
    global _api_key, _secret_key
    _api_key, _secret_key = None, None


def get_credentials():
    """The credentials this process was armed with by the browser."""
    if _api_key and _secret_key:
        return _api_key, _secret_key
    raise CredentialsMissing(
        "No Breeze credentials. Enter your App Key and Secret Key in the "
        "Account panel — they stay in this browser and are never saved on the "
        "server."
    )


def status():
    """Whether credentials are available and where from. Never the values."""
    if _api_key and _secret_key:
        return {"configured": True, "source": "browser",
                "fingerprint": fingerprint(_api_key)}
    return {"configured": False, "source": "none", "fingerprint": None}
