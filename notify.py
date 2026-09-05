"""Desktop notifications.

Uses macOS's built-in ``osascript`` so there is no extra dependency. The command
is passed as an argument list, never through a shell, so alert text cannot be
interpreted as a command. On other platforms this is a no-op and the dashboard
still shows fired alerts in the UI.
"""

import subprocess
import sys

TITLE = "Trading Assist"

# macOS attributes an ``osascript`` banner to Script Editor and offers no
# permission prompt for it, so an unpermitted banner is discarded silently with
# exit status 0. The sound needs no permission, so it is what actually gets
# noticed; the browser's own Notification API is the reliable visual channel.
SOUND = "/System/Library/Sounds/Ping.aiff"


def available(platform=None):
    """Whether desktop notifications can be sent on this platform."""
    return (platform or sys.platform) == "darwin"


def _escape(text):
    """Make text safe inside an AppleScript string literal."""
    flat = " ".join(str(text or "").splitlines())
    return flat.replace("\\", "\\\\").replace('"', '\\"')


def applescript(title, message):
    return (f'display notification "{_escape(message)}" '
            f'with title "{_escape(title)}"')


def _run(cmd):
    subprocess.run(cmd, check=False, capture_output=True, timeout=10)


def chime(platform=None, runner=None):
    """Play a short system sound. Returns True if one was dispatched."""
    if not available(platform):
        return False
    try:
        (runner or _run)(["afplay", SOUND])
        return True
    except Exception:
        return False


def send(title, message, platform=None, runner=None):
    """Show a desktop notification and play a sound. True if a banner was sent.

    Dispatching a banner is not the same as it being displayed — see SOUND.
    Never raises: a failed notification must not take down the alert watcher.
    """
    if not available(platform):
        return False
    try:
        (runner or _run)(["osascript", "-e", applescript(title, message)])
        shown = True
    except Exception:
        shown = False
    chime(platform=platform, runner=runner)
    return shown
