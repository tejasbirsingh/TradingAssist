# trading-app — market readout

Local dashboard over the ICICI Direct **Breeze** API.
Python 3.14 + `breeze-connect` 1.0.69 + FastAPI.

Reports **what price is doing** — trend state, indicator readings, momentum rank
against a 39-name universe, and honest data-sufficiency flags.

> It deliberately does **not** issue buy/sell recommendations. It used to. An
> 18-year backtest showed those verdicts had no out-of-sample edge, so they were
> removed rather than dressed up. See [Why there are no recommendations](#why-there-are-no-recommendations).
>
> Nothing here can place an order: `place_order`, `square_off` and the `gtt_*`
> methods are never imported.

## Run it

```bash
.venv/bin/uvicorn server:app --reload --port 8000
```

Open <http://localhost:8000>. First run: enter your App Key and Secret Key via
the **account avatar (top right)**, then the day's session ID in the **Session** tab.

## Layout

Three tabs, with the account control as an avatar in the top right:

| Tab | Contains |
|---|---|
| **Stocks** | Timeframe selector (Daily / 30-min / 5-min), the watchlist editor, and a readout card per symbol |
| **IPOs** | Open and upcoming issues. A green badge on the tab shows how many are open |
| **Session** | The daily session-ID form. A dot on the tab turns red when the session is dead |

The market-status banner sits above the tabs, since it applies everywhere.
The active tab is in the URL (`?tab=ipos&tf=5minute`), so a view is linkable and
survives a reload. Arrow keys move between tabs. If a request reveals an expired
session the app switches to the Session tab **once** — not on every 15-second
poll, so it cannot fight you while you are typing.

## Account (your Breeze credentials)

Click the **account avatar, top right** — it sits outside the tabs, since it is
setup rather than a view. A small dot on it shows status at a glance: green when
credentials are loaded, red when they are not. The popover dismisses on
outside-click or `Esc`, and opens itself on first run.

Enter your **App Key** and **Secret Key** there. There is no file to edit — a
fresh clone is configured entirely from the browser.

All three sensitive fields — App Key, Secret Key and the session ID — are
**masked as you type or paste**, each with an **eye toggle** to reveal. Masking
without a reveal is its own hazard: you cannot check a long pasted key for a
truncated or doubled character.

**Where they live:** `localStorage` in your browser, and nowhere else. On page
load the browser pushes them to the local server, which holds them **in memory
only** and never writes them to disk. "Forget on this device" clears both.

That matters because these keys are **order-capable** — they can place trades.
Never enter them into an instance you do not control.

**There is no server-side fallback.** A `.env` pair used to be honoured, but on a
deployed instance that meant requests could be served with whichever keys sat on
that host rather than the ones the visitor supplied. The browser re-arms the
process from `localStorage` on every load, so a restarted server picks the
credentials back up with nothing retyped.

The **session key is stored in the browser too**, for the same reason. A host with
an ephemeral disk — Render's free tier, for one — loses `data/session.json` on
every restart, and the daily `apisession` token cannot simply be replayed because
it is one-shot. So the page keeps the *exchanged* key and pushes it back:

| Step | |
|---|---|
| after an exchange | `GET /api/session/key` → saved to `localStorage` |
| on load | `GET /api/session`; if not ok, `POST /api/session/restore` |
| on restore rejection | the stored copy is dropped, so the same failure is not retried every load |
| on "Forget on this device" | both the credentials and the session key are cleared |

A restore is refused when the stored key's fingerprint does not match the App Key
now in use, which stops a swapped credential pair from reviving another account's
session. Validation happens before the file is touched, so a rejected restore
cannot destroy a working session.

This is the one place the session key leaves the server. It is safe in the same
sense the credentials already are: the browser is the durable store, and the App
Key and Secret it holds are strictly more powerful, since they can mint new
session keys.

Sessions are tagged with a **fingerprint of the App Key that minted them**
(SHA-256, first 12 chars — not reversible). Swap credentials and the stored
session is flagged *"belongs to a different App Key"* rather than silently
showing another account's data. Session files written before this existed are
still honoured.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/credentials` | `configured`, `source` (`browser`/`env`/`none`), key fingerprint. Never the values |
| `POST /api/credentials` | Body `{"api_key", "secret_key"}`. Memory only |
| `DELETE /api/credentials` | Forget them |
| `GET /api/config` | Whether Firebase is configured, and its web config |

`GET /api/session` deliberately **does not** return the App Key — the browser
builds its own login URL, so the key never leaves it.

### Google sign-in (required) and watchlist sync

`REQUIRE_LOGIN = True` in `app_config.py`, so the dashboard shows a **sign-in
screen** until you authenticate with Google. Signing in also syncs your
**watchlist** between machines. Your Breeze keys are **deliberately not**
synced — see below.

The page shows exactly one of three screens: a **loading** splash while the
sign-in state is being resolved, the **sign-in** screen, or the **dashboard**.
The splash comes first so an already-signed-in user never sees the sign-in prompt
flash before Firebase confirms their session, and it stays up after signing in
until the first data load finishes, rather than revealing an empty dashboard.

If Firebase never reports a sign-in state within 5 seconds — slow network, storage
blocked — the app falls back to the sign-in screen and says so, so it cannot hang
on a spinner. Background polling also pauses whenever the dashboard is not
visible, so signing out really does stop the traffic.

**What the gate does and does not do.** It stops the UI rendering and stops any
data being fetched — an unauthenticated visitor triggers exactly one request
(`/api/config`) and nothing else. It is **not** API security: the FastAPI
endpoints remain open to anything that can reach `127.0.0.1:8000`. On a
localhost-only app that is only you, but do not treat this as protection if you
ever expose the port. Real protection would mean verifying Firebase ID tokens
server-side.

Set `REQUIRE_LOGIN = False` to run without sign-in — also the escape hatch if
Firebase is unreachable and you get locked out. The gate tells you this on screen
rather than leaving you stuck.

Setup (console work only you can do):

1. Create a project at <https://console.firebase.google.com>.
2. **Build → Authentication → Sign-in method → Google → Enable.**
3. **Build → Firestore Database → Create database** (production mode).
4. Firestore **Rules** tab — paste this and Publish, so each account can reach
   only its own document:

   ```
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /users/{uid} {
         allow read, write: if request.auth != null && request.auth.uid == uid;
       }
     }
   }
   ```

5. **Authentication → Settings → Authorized domains** — `localhost` is there by
   default, so nothing to do **if you browse `http://localhost:8000`**. Firebase
   treats `127.0.0.1` as a *different* host and rejects it with
   `auth/unauthorized-domain`, so add `127.0.0.1` here only if you prefer that URL.
6. **Project settings → Your apps → Web app** → copy the config values into
   `firebase-web-config.json` (see `firebase-web-config.example.json`):

   ```bash
   cp firebase-web-config.example.json firebase-web-config.json
   ```

Restart the server. You will see the sign-in screen; after authenticating, the
account popover also shows who you are signed in as, with a Sign out link. On
first sign-in your local watchlist is pushed up; after that the stored list is
pulled down and every add/remove syncs.

Any Google account can sign in. If you ever host this, add an email allowlist —
Firestore rules already scope data per-account, but they do not restrict who may
authenticate.

**Why Breeze keys are not in Firestore.** A Firebase project owner can read every
document from the console, so storing secret keys there would put other people's
order-capable broker credentials in your custody. Firestore has no end-to-end
encryption, and any client-side key would itself have to live in the browser —
where the secret already is. So Firebase holds identity and the watchlist only.

The web `apiKey` is a public project identifier, not a secret; access is
controlled by the rules above. It is gitignored by default anyway, so a project
isn't published unintentionally — force-add it if you want to share one project
across your own machines.

Without `firebase-web-config.json`, or with an incomplete one, the feature is
silently off and the app behaves exactly as it does now. A stale code in a synced
list is skipped and reported rather than discarding the whole sync.

### Sharing this app

Give people the repo, not a URL. Each person runs their own copy with their own
Breeze app registration, which means their own credentials, their own session and
their own rate limit.

Hosting it for others does not work: it is single-tenant (one session file, no
auth), and you would become custodian of other people's order-capable broker
keys. Netlify in particular cannot run it at all — Netlify Functions support only
JavaScript, TypeScript and Go, with an ephemeral filesystem, while this is Python
and needs persistent disk for its caches.

## Session (the daily ritual)

**Do it in the dashboard** — the **Session** tab. A dot on the tab shows state,
and the panel gives the session's age plus a three-step form. The app switches to
this tab by itself when there is no usable session.

1. Click **Open the Breeze login page** (the app key is URL-encoded for you) and log in.
2. Copy the `apisession` value from the `https://127.0.0.1/?apisession=…` redirect.
3. Paste it in and press **Save session**.

No file editing, and the token is exchanged the instant you submit — which
matters, because of the two sharp edges in Breeze auth:

1. The `apisession` is **one-shot**. Exchanging it twice returns
   `Status 500 'Request Object is Null'` — which reads like a malformed request
   but actually means "already spent".
2. It goes **stale within minutes** of login.

What is kept is the `session_key` returned by that exchange, written to
`data/session.json`. `breeze_client.load_client()` rebuilds a signed client from
it and **never** calls `generate_session` again.

A rejected token leaves an existing good session untouched, and the error
explains the stale/spent case rather than echoing Breeze's misleading message.

`refresh_session.py` remains as a CLI alternative (it watches `.env` and fires
within 0.25s of a save). Both paths write through `breeze_client.store_session`,
so they cannot produce different files.

### Saving a session used to need a manual reload

Every tab refreshes itself when a session is saved — `refreshAll()` in
`static/index.html`, the same list boot uses. That was not enough, and the reason
was on the server, which is why reloading the page appeared to be the cure: it
was not, the elapsed time was.

`POST /api/session` drops the memoised client so the next call picks up the new
key. Rebuilding it was expensive in two ways that only a rebuild paid, both
measured on this machine:

| Cost on the first snapshot after a save | Before | After |
|---|---|---|
| `load_client()` — security-master download | 1.5s answering, **124.5s** not | **0.001s** |
| First snapshot end to end, cold process | **49.6s** | **0.416s** |

1. **`load_client()` called `get_stock_script_list()`.** The SDK downloads
   `SecurityMaster.zip` from `directlink.icicidirect.com` **twice** — once through
   `urlopen` with no timeout argument at all, once through `requests` whose
   response it then discards — and on failure *prints* rather than raises. Timed
   here at 1.5s when the CDN answered and 124.5s when it did not. It ran inside
   `GET /api/snapshot`. In `logs/apiLogs.log` for 8 Sep the session was saved at
   20:19:58 and the next Breeze call is at 20:20:36 — 38 seconds in which the
   server made no request at all. Nothing on the REST path reads those
   dictionaries; only the tick socket does, so `livefeed.load_symbol_map()` now
   loads them on the feed's own background thread, once per client, with a
   45-second ceiling so a hung download cannot leave the feed reporting
   `starting` for the life of the process.
2. **`market_data.client()` had no lock.** Endpoints are `sync def`, so FastAPI
   runs them on a threadpool: with the page polling every three seconds, every
   request that arrived during a rebuild started a rebuild of its own — ten
   concurrent downloads, all contending. It is now single-flight, and a *failed*
   build is still not memoised, so a session that starts working is picked up at
   once.

Profiling the cold path then showed a third stall with the same symptom:
`_retry` slept `attempts × pause` per symbol on a **missing session**, which is
not transient. The momentum ranking asks for 40 symbols, so 28 pointless sleeps
came to 16.9 of the first snapshot's 18.1 seconds. Session failures now raise
immediately; genuinely transient ones (Breeze returning a non-JSON body) still
retry.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/session` | Status: `ok`, `user_id`, `saved_at`, `age_minutes`, `app_key_fp`, `reason`. Never returns the session key or the App Key |
| `POST /api/session` | Body `{"session_token": "..."}`. Exchanges, persists, drops the cached client. `400` with a readable `detail` on failure |

The server binds to the loopback interface only, reachable as either
`localhost:8000` or `127.0.0.1:8000`. Use **`localhost`** — Firebase authorizes it
by default, while `127.0.0.1` needs adding to the authorized-domains list.

## IPOs

The **IPOs** tab lists issues open for subscription now and those forthcoming,
nearest deadline first. A **green badge on the tab** shows how many are open, and
the heading carries the urgent bit — e.g. *"2 open now, TEMPSENS closes
tomorrow"* — so a deadline is visible from any tab.

| Column | Notes |
|---|---|
| Status | **OPEN** / **UPCOMING** / CLOSED, computed from the issue dates, not NSE's `status` field (which can lag) |
| Price band | Plus lot size for SME issues |
| Window | "closes tomorrow", "2 days left", "opens in 3 days" — plus the raw dates |
| Subscribed | Times subscribed (`noOfTime`), live while the issue is open |

**This is the one section not sourced from Breeze.** Breeze has no IPO endpoint —
its 15 endpoints cover quotes, history, orders and portfolio only. The data comes
from two NSE public feeds, merged:

- `api/all-upcoming-issues?category=ipo` — the full list, EQ and SME
- `api/ipo-current-issue` — adds live subscription figures

Because it is third-party it is cached for 30 minutes and **falls back to a stale
copy** (clearly labelled) rather than emptying the section if NSE is unreachable.
It also keeps working when the Breeze session has expired.

SME issues are flagged: they are far smaller and less liquid than mainboard (EQ)
issues. You cannot apply for an IPO from this app.

### GMP is deliberately absent

**GMP (Grey Market Premium) is in no exchange or broker API.** Every key of both
NSE feeds was checked and the raw payloads searched for `gmp` / `grey` /
`premium` — nothing. That is structural: GMP is an unofficial price quoted by
over-the-counter grey-market dealers, and a regulated exchange will not publish an
unregulated off-market rumour. Trackers arrive at it by surveying dealers, which
is why sites disagree on the same issue on the same day.

Scraping a tracker was considered and rejected — brittle, and it would put an
unverifiable figure beside official data in an app whose buy/sell verdicts were
removed for having no demonstrated edge.

**Times subscribed** is the regulated, verifiable demand signal, and it is shown.

Also checked: the upcoming feed's `priceBand` key is redundant — `None` for every
mainboard issue and an exact duplicate of `issuePrice` for SME ones.

## Live prices (WebSocket)

During market hours the app opens Breeze's socket.io stream
(`livestream.icicidirect.com`) and subscribes to the watchlist. A **LIVE** badge
appears in the banner with a running tick count, and prices update every few
seconds with second-level timestamps. Outside market hours the feed is stopped
and prices come from the last REST quote.

### Does streaming consume the API quota?

**Not documented by ICICI** — the docs' streaming chapters are truncated, and the
rate-limit sentence says only "100 API calls per minute and 5000 API calls per
day" without mentioning sockets, subscriptions or ticks.

**Mechanically it cannot**: ticks arrive as server-pushed frames over one
persistent connection to a *different host*, not as HTTP requests against
`api.icicidirect.com/breezeapi/api/v1/*`.

**Measured**: 3 symbols produced ~0.4–1 tick/sec each, 64 ticks inside a minute,
while REST calls kept working normally throughout. Had ticks counted as API
calls, 100/min would have been breached almost immediately and REST would have
started failing. It did not.

**Why it matters**: polling 3 symbols on a 20-second TTL across one 6.25-hour
session costs roughly `375 min x 3/min x 3 symbols = 3,375` calls — about
two-thirds of the daily budget. Streaming reduces that to a handful, since the
REST quote is then needed only occasionally for the last-trade-time that drives
the holiday check.

### Outside market hours the socket is closed — and closing it took real work

`snapshot()` closes the feed whenever the clock is outside 09:15–15:30, so there is
no overnight connection. Two things had to be fixed before that was actually true.

**`stop()` was called without a client**, and it only disconnects when given one, so
the market-closed path cleared the flags and left the connection open for the rest of
the process's life while `status()` reported `connected: false`. `_stop_feed()` now
passes the client, falling back to the flag-only stop when no session is armed.

**The SDK's `ws_disconnect()` does not disconnect.** It calls the socket.io *event
handler* `on_disconnect()` and then sets its own reference to `None`, leaving the
underlying `socketio.Client` alive with its default `reconnection=True`. Measured
directly:

```
opened     -> connected: True   TCP 192.168.1.114:57029->99.86.30.25:443
ws_disconnect only            -> TCP 192.168.1.114:57033->99.86.30.25:443   ← new port
```

It reconnected within seconds. `LiveFeed.stop()` therefore closes the real socket
first — `client.sio_rate_refresh_handler.sio.disconnect()`, while the handle still
exists — and calls `ws_disconnect()` afterwards only for the SDK's bookkeeping. An
explicit `disconnect()` suppresses reconnection, verified quiet at 2s, 5s and 10s.

### The socket is only held open while someone is watching

Every `/api/snapshot` calls `feed.note_activity()`. The background watcher checks
`feed.should_idle()` each cycle and closes the socket once **`IDLE_AFTER = 120`
seconds** pass with no snapshot request — which is what happens when the last
dashboard tab is closed. Reopening the page calls `ensure_started()` again and the
feed reconnects and resubscribes.

The check lives on the watcher, not the request path, because the thing being
detected is an *absence* of requests. 120s is comfortably longer than the page's
3-second poll, so a brief network stall does not tear down a working feed.

**Consequence: alerts are only live while a tab is open.** With the socket down,
the watcher falls back to the on-disk quote cache, so an alert can fire late or
not at all. That is the deliberate trade for not holding a broker connection open
around the clock.

### Observed behaviour that shaped the implementation

| Finding | Handling |
|---|---|
| An immediate reconnect after a disconnect fails ~1 in 3 times | 4 connect attempts, 6s apart |
| Connecting can block for seconds | done on a background thread, never inside a request |
| Ticks identify instruments as `4.1!<token>`, not by stock code | token mapped back via the SDK's own token dictionary |
| A raising callback would kill the socket thread | `handle_tick` never raises |
| Breeze returns no forming *daily* bar mid-session | daily indicators legitimately use completed days only |

A tick's `last` is the LTP, `close` is the **previous** close and `change` is a
percentage. A tick older than 60s is treated as absent, so a silent socket falls
back to REST rather than showing a frozen price.

## Price alerts

The **Alerts** tab sets a level per symbol — *rises above* or *falls below* — with
an optional note. When it triggers you get a desktop pop-up and a sound, and the
tab shows a badge with the count of fired alerts.

**Evaluated on the server, every 5 seconds, against live tick prices.** That means
alerts fire even with the browser closed — the browser is only needed to manage
them.

### Three places a trip shows up

1. **A modal dialog**, on whichever tab is open, listing up to the four most
   recent trips with *View alerts* and *Dismiss*. This needs no permission of any
   kind, so it is the channel that always works while the page is open — and
   unlike a banner it cannot be scrolled past or read as part of the layout.
   Built on the native `<dialog>` element, so Esc closes it and focus is trapped
   while it is up. A second trip while it is open rebuilds the list rather than
   re-opening, which would steal focus again.
2. **A browser notification**, if permission was granted (see below).
3. **A sound**, played by the server.

The dialog and the notification both skip trips older than 10 minutes on a fresh
page load — those are already listed in the Alerts tab, and would otherwise
re-announce on every reload. Re-arming an alert clears its record, so the next
crossing announces again.

### Why the pop-up comes from the browser, not the server

The visible pop-up uses the **browser's Notification API**, requested by the
*Turn on notifications* button in the Alerts tab. It is the only channel that
asks permission, so it is the only one that reliably displays.

The server-side `osascript` banner in `notify.py` is **not reliable on its own**.
macOS attributes such a banner to Script Editor, offers no permission prompt for
it, and when that permission is absent it **discards the banner while still
exiting 0** — so the code looks like it succeeded and nothing appears. That cost
a debugging cycle: the alert had genuinely fired, `triggered_at` was written, and
no notification was ever seen.

So `notify.send()` also plays a system sound (`afplay`), which needs no
permission. Net effect:

| Channel | Needs permission | Works with the tab closed |
|---|---|---|
| Browser notification | yes, prompted | no |
| Sound (`afplay`) | no | yes |
| `osascript` banner | yes, **never prompted** | yes, if already granted |

On non-macOS platforms alerts still fire and appear in the UI; only the sound and
the banner are skipped.

**One-shot by design.** A price that stays past the line would otherwise re-notify
every 5 seconds, so a fired alert deactivates and shows a **Re-arm** button.

The watcher reads only the tick feed and the on-disk quote cache, so it **spends
no REST quota** however long it runs.

| Endpoint | Purpose |
|---|---|
| `GET /api/alerts` | Alerts, whether notifications are supported, and the watcher's last error |
| `POST /api/alerts` | Body `{code, direction, price, note}` |
| `DELETE /api/alerts/{id}` | Remove one |
| `POST /api/alerts/{id}/rearm` | Re-arm a fired alert |

A blanket `except: pass` in the watcher once hid a `NameError` for a whole test
cycle — alerts silently never fired and nothing complained. The watcher now
records its last error and `GET /api/alerts` reports it, which the UI displays.

## Deploying (Render)

`render.yaml` is a Blueprint: **New → Blueprint** in the Render dashboard, point it
at the repo, and it fills the form itself.

### The gate you must not skip

`REQUIRE_LOGIN` and the Google sign-in run **in the browser only**. They hide the
UI and protect nothing — on a public host `POST /api/credentials` would let a
stranger overwrite the order-capable Breeze keys, and `POST /api/session` would
let them install a session.

So `auth.py` adds an HTTP Basic gate over every path. Set **`AUTH_PASSWORD`** (and
optionally `AUTH_USER`, default `trader`) in Render's environment. With no password
set the gate is inactive, which is why local loopback use is unchanged.

`/api/config` stays open because Render pings it as the health check; it returns
local config only, and the Firebase web `apiKey` in it is a public project
identifier, not a secret.

### Firebase ID tokens as a second credential

`firebase_auth.py` verifies a Firebase ID token server-side, so Google sign-in can
become a real gate rather than a UI decoration. `auth.permits()` accepts **either**
credential, which is what lets the token be introduced without invalidating the
password a live deployment already depends on.

| env | effect |
|---|---|
| `AUTH_PASSWORD` | HTTP Basic accepted |
| `REQUIRE_FIREBASE_AUTH=true` **and** a `projectId` **and** `ALLOWED_EMAILS` | verified Bearer token accepted, for those addresses only |
| neither | gate inactive — loopback development |

Deliberately **not** inferred from `firebase-web-config.json` existing: that file is
on a development machine, and inferring from it would have started demanding tokens
on localhost the moment this shipped.

Tokens are verified locally against Google's x509 certificates rather than by calling
an API per request — no added latency, and no dependency on Google being reachable
while serving. Google rotates signing keys roughly daily, so an unknown `kid`
triggers one refetch; without it every user is locked out for the rest of the cache
window on each rotation. **Every failure path denies**, including a key-fetch failure:
unavailable is not the same as allowed.

`aud` and `iss` are both pinned to the project, because anyone can create a Firebase
project and sign perfectly valid tokens in it — a signature proves only that *Google*
issued the token, not that it was issued for us.

### Two defects an adversarial review found in this code

**The browser leaked the token cross-origin.** The `window.fetch` wrapper attaches
the ID token to same-origin requests, and the original test was
`url.startsWith("/") || url.startsWith(location.origin)`. Four URLs pass that and
resolve to an attacker's host:

```
//evil.example.com/x                        protocol-relative
https://ourhost.evil.com/x                  no boundary after the origin
https://ourhost@evil.example.com/x          "ourhost" is userinfo, not the host
/\evil.example.com/x                        backslash normalises to a slash
```

Now decided by `new URL(url, location.href).origin === location.origin`. Never gate a
credential on a string prefix.

**A non-ASCII Basic header returned an unauthenticated HTTP 500.**
`secrets.compare_digest` raises `TypeError` on non-ASCII `str`, uncaught, so
`Authorization: Basic <base64 of "usér:pass">` produced a traceback from inside the
gate. Compared as UTF-8 bytes now, which also makes a non-ASCII password genuinely
usable rather than merely safe.

What the review could **not** break: path-normalisation bypasses (`//api/alerts`,
`/API/alerts`, `/static/..%2f..%2fapi/alerts`, `/api/config/../alerts` — all 401),
`alg=none`, RS256→HS256 confusion using the public key as the HMAC secret, and
downgrade between the two mechanisms.

### Signing in is not the same as being allowed in

A Firebase project with the Google provider enabled accepts **any** Google account —
that is what the provider is for. So a verified token proves only that Google issued
it for this project, never that its owner is welcome, and `REQUIRE_FIREBASE_AUTH` on
its own is a *weaker* boundary than the password: it swaps one shared secret for
"anyone with a Gmail address".

**`ALLOWED_EMAILS`** is what closes it. Comma, semicolon or space separated,
case-insensitive, matched against the token's `email` claim:

```
ALLOWED_EMAILS=you@gmail.com, someone.else@gmail.com
```

Two properties worth stating, both pinned by tests:

- **Unset means nobody, not everybody.** Forgetting the list would otherwise be
  indistinguishable from publishing the app, so an empty list refuses every token
  and the server prints `AUTH WARNING:` at startup saying so.
- **An unverified address is refused.** An unverified `email` claim is an assertion,
  not a fact, so it cannot be matched against a list. Google sign-in always reports a
  verified one.

Measured behaviour of each configuration, over HTTP:

| `AUTH_PASSWORD` | `REQUIRE_FIREBASE_AUTH` | `ALLOWED_EMAILS` | `/api/config` | any other path |
|---|---|---|---|---|
| — | — | — | 200 | **200 — fully public**, plus a startup warning |
| set | — | — | 200 | 401 without Basic |
| — | on | set | 200 | 401 without an allowlisted token |
| — | on | empty | 200 | 401 always, plus a startup warning |

Forged tokens (`alg=none` carrying an allowlisted address, a signature-less RS256
header) are refused in every configuration.

**Both gates on at once is the safe transition, with one catch.** While signed in,
the browser's fetch wrapper *replaces* the `Authorization` header with the Bearer
token, so a signed-in-but-not-allowlisted account gets 401s even though the password
would have worked. The page now says exactly that instead of "cannot reach the
server". The way back in is to sign out of Google or open a private window: with no
token to send, the Basic prompt returns.

### Where the gate is enforced, and the one thing that must stay open

`server.py` registers `require_password` as **middleware**, not as a per-route
dependency, so a route added later is covered without anyone remembering to
annotate it. Probed with the token gate on, all 24 routes plus `/api/docs`, an
unknown path, and four traversal attempts return `401`:

```
200  84862B  GET /                                     <- the page
200  84862B  GET /static/index.html
200    237B  GET /api/config
401          GET /api/session/key    /api/watchlist    /api/snapshot   /api/health
401          POST /api/credentials   DELETE /api/credentials   PUT /api/watchlist
401          GET /api/docs           /nonexistent      /staticky/index.html
401          GET /static/../api/session/key            //api/watchlist
401          GET /static/..%2f..%2fapi%2fsession%2fkey /static/./../api/watchlist
```

**The app shell has to be open, and that is not a compromise.** Gating `/` under a
token-only deployment is a permanent lockout rather than a prompt: the page is what
loads the Firebase SDK, so with no page there is no sign-in, no token, and no way to
acquire one — and with `AUTH_PASSWORD` deleted the 401 carries no
`WWW-Authenticate`, so the browser cannot even ask. `index.html` is already public
on GitHub and holds no data; every value it displays arrives from a gated `/api/`
route.

`auth.open_path()` normalises with `posixpath.normpath` before matching the
`/static/` prefix, because ASGI hands over an already-percent-decoded path — without
that, `/static/..%2f..%2fapi/session/key` arrives as `/static/../../api/session/key`,
matches the prefix, and walks straight out of the shell.

### Settings that matter

| Setting | Why |
|---|---|
| `--workers 1` | The Breeze client, credentials and tick feed are module-level state. A second worker holds its own empty copy and serves blank data on half the requests. |
| `--host 0.0.0.0` | Binding loopback as we do locally makes the service unreachable. |
| `TZ=Asia/Kolkata` | `alerts.py` stamps `triggered_at` with a naive `datetime.now()`. On a UTC host those sit 5h30m behind the IST times shown elsewhere, and the page-load freshness check would treat a fresh trip as stale and skip announcing it. |
| `healthCheckPath: /api/config` | Never calls `note_activity()`, so health pings cannot keep the tick socket alive and defeat the idle shutdown. |

### What the free tier costs you

- **The filesystem is ephemeral.** `data/session.json` is wiped on every spin-down,
  so the session ID must be re-entered after each cold start.
- **It sleeps after ~15 minutes** of no traffic, and wakes in 30–60s. That happens
  to match the idle-socket behaviour above, but it also means **alerts do not fire
  while no tab is open**.
- **No desktop sound.** `notify.py` is macOS-only, so on Linux only the dialog and
  the browser notification remain.
- Add the `*.onrender.com` host to Firebase **Authentication → Settings →
  Authorized domains**, or sign-in fails with `auth/unauthorized-domain`.

Memory is not a concern: the process sits around 196 MB against a 512 MB limit.

**Still single-tenant.** `credentials.py` holds the keys in module globals, so a
second user overwrites the first. Don't share the URL until that changes.

## Rate limits shape the architecture

Breeze allows **100 calls/minute and 5000/day**, so the **browser polls this
server, never Breeze**. Daily candles cache for 12h when the market is shut;
quotes for 20s while open. A three-symbol watchlist costs ~15 calls/day.

Polling Breeze directly every 15s would have exhausted the daily quota in ~4h.

## What the dashboard shows

| Field | Meaning |
|---|---|
| **Trend state** | UPTREND / DOWNTREND / RANGE-BOUND / MIXED / TREND UNKNOWN — position relative to the 20- and 50-day averages. A fact, not a suggestion |
| **Momentum (12-1)** | Rank against the cached universe by trailing 12-month return ending one month ago. Momentum is cross-sectional, so a rank is meaningful where a bare number is not |
| **data** | Sufficiency, not conviction: INSUFFICIENT / LOW / MEDIUM / HIGH by bar count |
| **Indicator table** | RSI(14), price vs 20/50SMA, volume vs 20d, range position, each with a plain-English reading |
| **Amber boxes** | Warnings about the *data*, e.g. recently listed, too few bars |

Everything is explained on hover or keyboard focus. The **trend-state badge is
its own hover target** (dotted underline marks it); smaller fields — data
confidence, each indicator, momentum — use a **ⓘ** bubble instead, since they
are too small to be comfortable targets.

The text lives in `readout.py` next to the logic and is served with the payload,
so the numbers quoted in a tooltip cannot drift from the thresholds actually used
— `tests/test_glossary.py` fails if a state or indicator is added without an
explanation.

### Trend states

| State | Means |
|---|---|
| **UPTREND** | More than 2% **above** both the 20- and 50-day average. Both timeframes agree it is rising |
| **DOWNTREND** | More than 2% **below** both averages. Both agree it is falling |
| **RANGE-BOUND** | Within ±2% of **both** averages — drifting sideways around its own average, no direction established |
| **MIXED** | The averages disagree: clearly above one, clearly below the other. Usually a turning trend, or a sharp recent move against a longer one |
| **TREND UNKNOWN** | Fewer than 50 bars, so the 50-day average does not exist yet. Says nothing about the stock — only about the data |

### Why DHOTRA shows TREND UNKNOWN

Dhoot Transmission listed **17 Aug 2026** with 5 daily candles. RSI(14) needs 15,
SMA20 needs 20, SMA50 needs 50 — all mathematically unavailable. The 5-min tab
gives computable values, capped at LOW confidence with a price-discovery caveat.

## The next-day forecast experiment

`forecast_experiment.py` tests directly whether a model trained on this app's own
features predicts tomorrow's return well enough to be worth displaying. Run it with
`pip install -r requirements-dev.txt` first — scikit-learn is deliberately kept out
of `requirements.txt` so the server still fits Render's 512 MB.

The decision rule was **fixed in the source before any result was seen**, because the
failure mode of this experiment is not a bug but a rationalisation afterwards.

**Result: all three rules failed.** 162,061 rows, 39 names, 2009–2026, walk-forward
by calendar year with the model only ever seeing data from before the year it is
judged on.

| Measure | Outcome |
|---|---|
| Mean absolute next-day move | 1.310% |
| **Breakeven directional accuracy** at a 0.30% round trip | **61.4%** |
| Logistic regression / gradient boosting achieved | **50–52%** |
| Beat the always-up baseline by >1pp | 7/15 and 8/15 years |
| Ridge vs the persistence baseline on MAE | **lost in 14 of 15 years** |
| Mean net return per trade | **−0.215%** and **−0.199%** |

The breakeven number is the one that matters: you need to be right **61.4%** of the
time on daily direction merely to cover costs. Published daily-direction models on
liquid equities rarely clear 55%, and these cleared 52%.

The always-up baseline is used rather than 50% on purpose — equities drift upward, so
a model must beat *that*, and in several years it did not.

### Two defects this experiment surfaced

**Single-bar price spikes in Breeze's daily history.** 195 bars across 27 of 39 names
where the close is 5x or 10x both neighbours and reverts immediately — ASIPAI prints
**+901%** on 2011-10-26, and the same dates recur across unrelated names. NSE price
bands make a real +900% day impossible. Before filtering these, the mean daily move
read 2.195% instead of 1.418%, which *understated* the breakeven bar as 56.8%, and a
handful of spikes in the traded set manufactured a false "profitable" result.
`drop_bad_bars()` removes them. **These bars are still in `data/history/` and the
dashboard's own long-window indicators read them** — filtering them in the app is not
yet done.

**Adjusted-price consistency.** The experiment consumes `splits.adjust_bars` output,
so the features are on the same footing as the dashboard's. Its vectorised RSI is
checked against `indicators.rsi` on every name and agrees to 0.0000.

## Why there are no recommendations

The app originally scored five indicators into BUY / SELL / HOLD. `backtest.py`
tested it properly and it did not survive.

```bash
.venv/bin/python universe.py     # resolve + validate 40 NSE codes
.venv/bin/python backtest.py     # walk-forward, non-overlapping, costed
```

Method: 39 NSE names, **4,433 daily bars each (2008–2026)**, non-overlapping
20-day windows, chronological 70/30 split, 0.30% round-trip cost, benchmarked
against buy-and-hold. No look-ahead — enforced by a test.

**Result on 8,460 non-overlapping observations:**

| | benchmark | BUY | HOLD | SELL |
|---|---|---|---|---|
| in-sample | +4.26% | **+2.84%** | +4.85% | +3.46% |
| out-of-sample | +0.92% | +1.19% | +0.79% | +1.29% |

In-sample, **BUY underperformed the model's own HOLD bucket by 2pp**.
Out-of-sample the three buckets are indistinguishable.

Properly-specified **cross-sectional 12-1 momentum** was then tested as the
best-evidenced alternative (Jegadeesh & Titman 1993, replicated over two
centuries). Across 8 parameterisations — horizons 21/63d, top 20%/30%, with and
without an absolute-momentum filter — in-sample excess returns were large but
never significant (all t < 1.35), and **out-of-sample excess was negative in all
8**.

Every variant flips sign between the two halves, which is the signature of noise:

| variant | in-sample spread | out-of-sample spread |
|---|---|---|
| full model | +0.31% | −2.39% |
| RSI reversed | +0.48% | −2.41% |
| trend only | +0.15% | −4.78% |
| mean-reversion only | −1.60% | **+3.74%** |

**Correction (2026-08-27): most of the "survivorship bias" was a data bug.**

This section previously reported an in-sample benchmark annualising to **65%/yr**
against a real Indian large-cap 12–14%, and attributed the whole gap to
survivorship bias. That attribution was wrong. `load_universe_candles` fed the
harness raw Breeze bars, so the benchmark was counting **1:1 bonuses as −50% days
and NSE special-session bars as +100% days**. The harness now reads through the
same pipeline as the dashboard — `dataquality.clean_bars` then
`splits.adjust_bars` — and on 8,466 non-overlapping observations across 40 names:

| | benchmark per 20d | annualised |
|---|---|---|
| in-sample | **+2.11%** | ~30%/yr |
| out-of-sample | **+1.11%** | ~15%/yr |

### A third defect: fetch-window seams

Found by plotting the close series — something no test had ever done. `history.py` pages
backwards in 3-year windows and Breeze adjusts its history retroactively, so each window
arrived with the adjustment state Breeze had *at fetch time*. Joining them left **permanent
scale breaks at the seams**: 79 of them across 25 of 40 names, on three dates, at factors
that are cumulative split ratios.

| seam | names | sample factors |
|---|---|---|
| 2011-01-03 | 21 | `RELIND 0.498`, `HDFBAN 0.1018`, `ITC 0.6669`, `BHAELE 0.033` |
| 2015-10-01 | 16 | |
| 2015-10-06 | 15 | |

Neither existing guard could see them. `find_artifacts` tests for a *reverting* spike, and a
seam persists; `applicable_events` only fires on dates matching a Yahoo split, and these are
fetch-window boundaries.

`seam_dates()` + `repair_splices()` fix it, and the safety comes from a **cross-sectional**
test: a seam is an artifact of when data was fetched, so it lands on one date across many
unrelated companies, while a real collapse is idiosyncratic. Requiring agreement across ≥5
names means a genuine crash is never flattened. 27 idiosyncratic steps are deliberately left
alone for exactly that reason.

Effect on the universe: permanent >30% steps **79 → 27**, daily return sd **5.21% → 2.33%**,
and HDFBAN's 18-year return **+18% → +1075%**.

**The out-of-sample figures above never moved.** All three seams fall inside the in-sample
70%, so the conclusion that matters was never contaminated — only the in-sample benchmark
shifted, from +1.85% to +2.11%.

~15%/yr out-of-sample is a believable Indian large-cap figure, not an artifact.
Survivorship bias is still real — the universe is 40 names that are large caps
*today*, and fixing that needs point-in-time constituents nobody free provides —
but its magnitude was overstated by a fixable bug, and the honest statement is that
the residual bias is small rather than "severe".

**The no-edge conclusion survives the correction, and reads more cleanly.**
Out-of-sample, `BUY` returns **+0.90%** against a **+1.11%** benchmark — it
*underperforms* simply holding — while `SELL` returns **+1.28%**, still inverted.
Strategy P&L is +0.08% per 20 days at t=+1.46, indistinguishable from zero, and it
does not beat buy-and-hold.

Two methodology traps the harness caught:

- Ranking variants by total P&L rewards whichever trades *most* (more market
  exposure), not whichever picks better. `selection_skill()` compares BUY vs SELL
  forward returns instead, neutral to trade frequency.
- Picking the best in-sample variant and shipping it is data-snooping. Demonstrated:
  the best in-sample variant scored **−0.40% (t=−2.35)** out-of-sample.

**Conclusion:** two well-specified attempts failed out of sample, on data with a
bias that cannot be fixed from this API. Continuing to tune would be snooping,
not progress. The indicator readings remain genuinely informative; the verdicts
did not, so they are gone.

## Breeze data quirks handled

Found by driving the app against live data — all real, all were producing wrong
output:

| Quirk | Real example | Handling |
|---|---|---|
| 1000-bar cap per request | asking from 2000 or 2018 both return the same 1000 bars | `history.py` pages backwards in 3-year windows and de-duplicates |
| Zero-volume artifact bars | 15:30 closing stamp, 09:05 pre-open | bar kept (its close is valid), excluded from volume stats |
| **Negative volume** | RELIND 2026-08-21 15:25 → `-4,865,882` | clamped to 0; a trade correction, not a real figure |
| Closing-auction spike | RELIND 5-min → `62.66x` the 20-bar mean | above 10x the volume reading is flagged as an auction artifact |
| Two venues per quote | `get_quotes` returns NSE **and** BSE rows | filtered to NSE; taking `[0]` can show the wrong venue |
| Name-matched codes are unsafe | "Axis Bank" fuzzy-matched `AXBETF`, an **ETF** | `universe.py` uses explicit codes, validated against the master |
| **Prices are as-traded, so splits read as crashes** | RELIND 2024-10-25 `2655.70` → 10-28 `1334.35`, a 1:1 bonus scored as −49.8% | `splits.py` restates history in today's share terms before momentum is scored |

### Split and bonus adjustment

Breeze returns raw traded prices and nothing in the app adjusted them, while
`momentum_score` is a bare percentage change over 252 sessions. A share
subdivision therefore scored as a catastrophic loss:

| Code | Score before | After | Rank before | After |
|---|---|---|---|---|
| `KOTMAH` | −80.7% | **−2.8%** | 39/39 | 27 |
| `HDFBAN` | −62.3% | **−25.4%** | 38 | 35 |
| `NESIND` | large negative | **+32.5%** | bottom | **4** |

And because momentum is a **rank**, the damage was not confined to those names:
ten others — `TCS`, `WIPRO`, `ITC`, `RELIND` among them — held correct scores but
wrong positions, purely because two names were wrongly placed below them.

Ratios come from Yahoo via `yfinance` and are cached in `data/splits.json`:

```bash
.venv/bin/python refresh_splits.py     # run once, and after adding a stock
```

**Inferring ratios from the price step was rejected.** Snapping a ~50% overnight
drop to "probably a 2:1 split" works on this data, but a genuine one-day collapse
is rare rather than impossible, and guessing would erase a real disaster. Yahoo
publishes the effective date and exact factor, so it is a fact rather than a
guess — cross-checked against Breeze's own bars: the adjusted 2024-10-25 close is
`1327.85`, which is exactly what Yahoo reports for its own adjusted series.

### Breeze is inconsistent, so the ratio is verified before it is applied

**Trusting the ratio blindly was a bug**, caught by an adversarial review. Of the 59
cached events that fall inside the history, **27 leave the raw overnight step in
place and 31 arrive already restated by Breeze**:

| | |
|---|---|
| `RELIND` 1:1 bonus, 2024-10-28 | `2655.70 → 1334.35` — step present, must be adjusted |
| `TATSTE` 10:1 split, 2022-07-28 | `96.04 → 100.20` — continuous, already adjusted |

Applying the ratio to an already-adjusted series divides those bars a **second**
time, which is worse than leaving them raw — `TATSTE`'s pre-2022 closes became
about a tenth of their real value.

So `applicable_events()` asks the data instead of assuming: an event is applied only
if the closes either side of it actually move by roughly the ratio
(`STEP_TOLERANCE = 0.15`). Across the universe that applies **27** and skips **64**.
It also makes events outside the data harmless, which is why `JSWSTE`'s odd 2005
ratio can never fire on history that starts in 2008.

**Nothing about this touches the live path.** `yfinance` is imported lazily inside
`splits.fetch`, which only `refresh_splits.py` calls. The request path reads the
cached JSON, and a missing file means momentum is unadjusted exactly as before —
so a Yahoo outage costs nothing. Prices, quotes, the tick socket and alerts remain
100% Breeze; Yahoo's NSE data is **15 minutes delayed** (`exchangeDataDelayedBy=15`)
and could never back a live feed.

Two things this does **not** fix: the backtest's survivorship bias (Yahoo purges
delisted Indian equities, so point-in-time constituents are unavailable) and the
252-session requirement for new listings (`DHOOTTRANS.NS` has 6 bars on Yahoo too
— a company listed last week has no history anywhere).

One oddity to know: Yahoo reports `JSWSTE 2005-02-21 ÷0.04375`, almost certainly a
merger share-exchange rather than a true reverse split. It predates that series'
history so it is never applied, but it would matter if history were extended back.

## Watchlist

**Edit it in the dashboard** — the Watchlist block in the **Stocks** tab. Current
holdings appear as chips with an **×** to remove; type a company name or code to
search NSE and click a result to add. Changes persist to `data/watchlist.json` and take effect
on the next refresh.

Search matters because the Breeze code is usually *not* the chart ticker — you
would have no way to guess `DHOTRA` for Dhoot Transmission. Typing "reliance"
finds `RELIND`; "tata motors" finds both post-demerger entities.

Every added code is validated against the security master and rejected if it is
not a plain NSE equity:

| Input | Result |
|---|---|
| `tcs` | added as `TCS` — Tata Consultancy Services Ltd |
| `RELAINCE` | rejected — "not a tradable NSE equity code" |
| `AXBETF` | rejected — "is 'Axis Banking Etf', which is not plain equity" |
| duplicate | rejected — "already in the watchlist" |

`app_config.WATCHLIST` is only the initial seed; once saved, the file wins.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/watchlist` | Current list |
| `POST /api/watchlist` | Body `{"code": "..."}`. Validates, then persists |
| `PUT /api/watchlist` | Body `{"codes": [...]}`. Replaces the list; reports codes it skipped |
| `DELETE /api/watchlist/{code}` | Removes one code |
| `GET /api/symbols?q=` | Search NSE equities by name or code |
| `GET /api/ipos` | Open and upcoming IPOs (NSE feed, 30-min cache) |

`code` must be the Breeze **ShortName**, not the chart ticker:

| Company | Chart ticker | Breeze `code` | Listed |
|---|---|---|---|
| Dhoot Transmission | DHOOTTRANS | `DHOTRA` | 17 Aug 2026 |
| SBI Funds Management | SBIFUNDS | `SBIFUN` | 21 Jul 2026 |
| Reliance Industries | RELIANCE | `RELIND` | — |


## Files

| File | Purpose |
|---|---|
| `refresh_session.py` | Daily token exchange + harvest |
| `credentials.py` | Per-user app credentials, browser-supplied, memory only |
| `auth.py` | HTTP Basic gate over every path, active when `AUTH_PASSWORD` is set |
| `firebase_config.py` | Optional Firebase web config (identity + sync only) |
| `breeze_client.py` | Rebuilds a client from the saved session key |
| `market_data.py` | Fetch + cache candles/quotes, market-open detection |
| `livefeed.py` | WebSocket tick feed (replaces polling during market hours) |
| `alerts.py` | Price alerts: model, storage, one-shot trigger logic |
| `notify.py` | Desktop notifications via macOS osascript |
| `history.py` | Paginated multi-year history past the 1000-bar cap |
| `indicators.py` | Pure maths — SMA, Wilder RSI, volume, range |
| `signals.py` | Indicator readings and data-sufficiency |
| `momentum.py` | Cross-sectional 12-1 momentum |
| `splits.py` | Split/bonus ratios and history restated in today's share terms |
| `refresh_splits.py` | One-off CLI that fills `data/splits.json` from Yahoo |
| `readout.py` | Trend state and momentum rank (no verdicts) |
| `backtest.py` | Walk-forward harness, costs, ablation, cross-sectional |
| `universe.py` | Validated 40-name NSE backtest universe |
| `server.py` | FastAPI: `/api/snapshot`, `/api/health` |
| `static/index.html` | Dashboard |
| `watchlist.py` | User-editable watchlist, validated against the master |
| `ipo.py` | Open/upcoming IPOs from NSE (Breeze has no IPO API) |
| `app_config.py` | Seed watchlist, thresholds, cache TTLs |
| `tests/` | 382 unit tests, no network |

`app_config.py` is **not** named `config.py` on purpose: the Breeze SDK does a
bare `import config` internally, so a top-level `config.py` shadows it and breaks
`breeze_connect` at import time.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Known gaps

- **Live tick streaming has never run through a full session.** It is wired up
  and verified for minutes at a time (see *Live prices* above), but not across
  09:15–15:30, so it is unknown whether the LIVE badge survives a whole day or
  the socket goes quiet and silently falls back to REST.
- **Google sign-in and Firestore watchlist sync have never succeeded once.** The
  init chain is confirmed (SDK loads, `initializeApp` returns, the button
  renders) but the only outcome ever observed from a real click was
  `auth/unauthorized-domain`. Treat both paths as unproven.
- The backtest universe has survivorship bias (above). Any future signal work
  needs point-in-time constituents from another data source.
- **Breeze's REST calls have no timeout.** The SDK calls
  `requests.get(url, data=..., headers=...)` with none, so a network stall is
  bounded only by the OS — `logs/apiLogs.log` is full of connect timeouts to
  `api.icicidirect.com`. A snapshot can therefore still block for a long time on
  a bad connection, and there is no clean fix from outside the SDK:
  `socket.setdefaulttimeout` is global and would also break the long-lived tick
  socket. The three stalls documented under *Session* were all removable; this
  one is not.
