"""Optional Firebase web config, read from ``firebase-web-config.json``.

Firebase is used only for **identity and watchlist sync** — never for Breeze
credentials. Those stay in the browser's localStorage, because the owner of a
Firebase project can read every Firestore document from the console, so storing
secrets there would put other people's order-capable broker keys in your custody.

Firebase's web ``apiKey`` is a public project identifier, not a secret; access is
controlled by Firestore security rules. The file is therefore safe to commit if
you want to share one project across your own machines.

Absent or incomplete config means the feature is simply off, and the app behaves
exactly as it did before.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "firebase-web-config.json"

# Without these the browser SDK cannot initialise, so a partial file is treated
# as "not configured" rather than half-enabling the UI.
REQUIRED_FIELDS = ("apiKey", "projectId", "appId")


def load():
    """The web config dict, or None when Firebase is not set up."""
    if not CONFIG_PATH.exists():
        return None
    try:
        config = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(config, dict):
        return None
    for field in REQUIRED_FIELDS:
        value = config.get(field)
        if not isinstance(value, str) or not value.strip():
            return None
    return config


def status():
    """Payload for the browser: whether Firebase is on, its config, and whether
    a sign-in is required before the dashboard may be used.

    ``require_login`` is reported even when Firebase is unconfigured, so the
    client can explain the lockout instead of silently showing nothing.
    """
    import app_config

    config = load()
    return {
        "enabled": config is not None,
        "firebase": config,
        "require_login": bool(getattr(app_config, "REQUIRE_LOGIN", True)),
    }
