"""Off-device delivery, so a reminder survives a shut laptop.

Channels were chosen by research rather than preference:

**SMS is not available.** India's DLT regime requires a registered Principal
Entity, physical verification and biometric authentication of the authorised
person, at ~Rs 5,900/yr, and there is no message category a monthly personal
reminder fits -- "transactional" means within 30 minutes of a customer-initiated
transaction, and "promotional" is DND-scrubbed. The apparent free tiers do not
help: Twilio's trial permits only its own fixed message templates, so the string
"ABH closes today" cannot be sent on it at any price, and Fast2SMS's no-DLT route
explicitly bans personal messages. The only genuinely free real-SMS path is your own
phone acting as the gateway, which is P2P and outside the regime entirely.

**Email works, but not from everywhere.** Render's free tier blocks outbound traffic
on ports 25, 465 and 587, so SMTP there fails at TCP connect and the password is
never transmitted. On a machine you control, Gmail with an app password is the
least-friction option and has the best deliverability, because Google relays the
message from its own IPs with SPF and DKIM aligned -- the sending host's reputation
never enters into it.

**Telegram is the closest free equivalent to an SMS**, and it runs over HTTPS on 443
so it works where SMTP is blocked. Its risk is political rather than technical: India
blocked the platform nationwide for about a week in June 2026.

Both channels are optional and off unless configured. Both fail soft and report why,
because at a handful of messages a month a revoked credential would otherwise go
unnoticed for months.
"""

import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

TIMEOUT = 20
TELEGRAM_LIMIT = 4096          # hard limit on sendMessage text

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465        # implicit TLS; 587 is STARTTLS


def _clean(env, name):
    return (env.get(name) or "").strip()


def configured(env=None):
    """Which channels have complete configuration. Order is stable for reporting."""
    env = os.environ if env is None else env
    out = []
    if _clean(env, "SMTP_USER") and _clean(env, "SMTP_PASSWORD"):
        out.append("email")
    if _clean(env, "TELEGRAM_BOT_TOKEN") and _clean(env, "TELEGRAM_CHAT_ID"):
        out.append("telegram")
    return out


def email_settings(env=None):
    """Resolved SMTP settings, defaulting to Gmail."""
    env = os.environ if env is None else env
    try:
        port = int(_clean(env, "SMTP_PORT") or DEFAULT_SMTP_PORT)
    except ValueError:
        port = DEFAULT_SMTP_PORT
    user = _clean(env, "SMTP_USER")
    return {
        "host": _clean(env, "SMTP_HOST") or DEFAULT_SMTP_HOST,
        "port": port,
        "user": user,
        # Google shows the app password as four groups of four, and shell quoting
        # routinely carries those spaces into the variable.
        "password": _clean(env, "SMTP_PASSWORD").replace(" ", ""),
        "to": _clean(env, "ALERT_EMAIL_TO") or user,
    }


def send_email(subject, body, env=None, smtp_factory=None):
    """(ok, detail). Never raises."""
    env = os.environ if env is None else env
    if "email" not in configured(env):
        return False, "Email is not configured (SMTP_USER and SMTP_PASSWORD)."

    settings = email_settings(env)
    factory = smtp_factory or smtplib.SMTP_SSL
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["user"]
    message["To"] = settings["to"]
    message.set_content(body)

    try:
        # A timeout is not optional: without one a blocked port hangs the watcher
        # thread rather than failing, which is how Render's free tier behaves.
        with factory(settings["host"], settings["port"], timeout=TIMEOUT) as smtp:
            smtp.login(settings["user"], settings["password"])
            smtp.send_message(message)
        return True, f"sent to {settings['to']}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _post(url, data):
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode("utf-8"))
    with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def send_telegram(subject, body, env=None, poster=None):
    """(ok, detail). Never raises."""
    env = os.environ if env is None else env
    if "telegram" not in configured(env):
        return False, ("Telegram is not configured "
                       "(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID).")

    token = _clean(env, "TELEGRAM_BOT_TOKEN")
    text = f"{subject}\n\n{body}"[:TELEGRAM_LIMIT]
    try:
        (poster or _post)(f"https://api.telegram.org/bot{token}/sendMessage",
                          {"chat_id": _clean(env, "TELEGRAM_CHAT_ID"),
                           "text": text, "disable_web_page_preview": "true"})
        return True, "sent"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def dispatch(subject, body, env=None, smtp_factory=None, poster=None):
    """Send on every configured channel. One channel's failure never stops another.

    Redundancy is the point: a deadline reminder delivered nowhere is the failure
    mode worth engineering against, and the two channels fail for unrelated reasons.
    """
    env = os.environ if env is None else env
    results = []
    for channel in configured(env):
        if channel == "email":
            ok, detail = send_email(subject, body, env=env,
                                    smtp_factory=smtp_factory)
        else:
            ok, detail = send_telegram(subject, body, env=env, poster=poster)
        results.append({"channel": channel, "ok": ok, "detail": detail})
    return results
