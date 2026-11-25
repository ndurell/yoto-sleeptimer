# yoto-sleeptimer

Automatically set a sleep timer on your Yoto players.

Whenever a Yoto player starts playing and no sleep timer is running, this
sets one (90 minutes by default). Audio then always stops on its own, even
if nobody remembers to set a timer at bedtime. A timer someone sets by hand
in the Yoto app is left alone.

It's a small Python service that runs all the time on something that's
always on, like a home server, a NAS, a mini PC or a Raspberry Pi. It runs in Docker
(recommended) or as plain Python.

```
2026-11-24 19:42:10 INFO Watching 2 player(s): Bedroom (y1a2b3...), Playroom (y4d5e6...)
2026-11-24 19:42:11 INFO Connected; waiting for playback.
2026-11-24 19:58:03 INFO Armed 90-min sleep timer on Bedroom
```

> This is an unofficial community project. It is not made by or affiliated
> with Yoto. It uses Yoto's public developer API through the community
> [`yoto_api`](https://github.com/cdnninja/yoto_api) library.

## Contents

- [How it works](#how-it-works)
- [What you need](#what-you-need)
- [Setup](#setup)
  1. [Create a Yoto developer app](#1-create-a-yoto-developer-app-get-a-client-id)
  2. [Get the code and configure it](#2-get-the-code-and-configure-it)
  3. [Sign in to Yoto (once)](#3-sign-in-to-yoto-once)
  4. [Start it](#4-start-it)
- [Settings](#settings)
- [Day-to-day](#day-to-day)
- [Running without Docker](#running-without-docker)
- [Adding it to an existing Docker Compose setup](#adding-it-to-an-existing-docker-compose-setup)
- [Moving from an older Yoto app](#moving-from-an-older-yoto-app-no-scopes-or-device-code)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)
- [Development](#development)

## How it works

1. On start it signs in to Yoto with a saved refresh token and finds the
   players on your account.
2. It opens a live connection to Yoto (MQTT over a secure WebSocket, the
   same channel the Yoto app uses) and listens for playback events. Nothing
   is polled.
3. When a player reports **playing** and **no sleep timer active**, it sends
   that player a "set sleep timer" command.
4. It won't set a timer on the same player again for 2 minutes (one press
   of play can send a burst of events), and it never replaces a timer that
   is already running.
5. If the connection drops it reconnects after 30 seconds, forever.
6. Yoto replaces the refresh token from time to time. The service saves
   each new one, so it keeps working across restarts without signing in
   again.

The timer starts when playback starts. If a child pauses and presses play
again after the timer ran out, a fresh timer is set.

## What you need

- One or more Yoto players on your Yoto account.
- An always-on machine with internet access. Anything that runs Docker
  works: a Linux box, a NAS, a Mac, a Windows PC with Docker Desktop, or
  a 64-bit Raspberry Pi. It needs very little memory or CPU.
- **Docker** with the Compose plugin. On Linux:
  ```bash
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER   # then log out and back in
  ```
  Or **Python 3.10+** if you'd rather [skip Docker](#running-without-docker).
- About 15 minutes for setup.

## Setup

### 1. Create a Yoto developer app (get a client ID)

The service talks to Yoto as an "app" that you register yourself. It's
free and takes a couple of minutes.

1. Go to the Yoto developer site, [yoto.dev](https://yoto.dev), and sign
   in to the developer dashboard with your Yoto account.
2. Create a new application:
   - **Type:** a *public* client (a native / command-line app). This
     project never uses a client secret.
   - **Callback / redirect URL:** add exactly
     ```
     http://127.0.0.1:8787/callback
     ```
     (If you want a different port, register that URL instead and set
     `YOTO_REDIRECT_URI` to match; see [Settings](#settings).)
   - **Scopes / permissions:** these are required. Tick `offline_access`
     (sign-in fails without it, and it's what lets the service stay
     signed in), plus the permissions to see your players and send them
     commands. Pick the ones the dashboard describes as viewing devices /
     players and controlling them. Content scopes such as
     `user:content:manage` are for cards and playlists, which this
     service doesn't need.
3. Copy the app's **client ID**. You'll need it in the next step.

New apps start out "unverified". That only means Yoto shows a warning on
the sign-in page; it's fine for your own use.

> **Already have an older Yoto app?** If you made one before Yoto added
> scopes, or you signed in with the old "enter this code" (device-code)
> flow, see [Moving from an older Yoto app](#moving-from-an-older-yoto-app-no-scopes-or-device-code).

### 2. Get the code and configure it

On the machine that will run it:

```bash
git clone https://github.com/ndurell/yoto-sleeptimer.git
cd yoto-sleeptimer
cp .env.example .env
mkdir -p data
```

Open `.env` in an editor and set at least:

```ini
YOTO_CLIENT_ID=paste-your-client-id-here
TZ=Europe/London          # your time zone, e.g. America/Chicago
```

Also check `PUID` / `PGID`: they're the user and group the container runs
as, and must own the `data` folder. Run `id -u` and `id -g` to see yours
(on most Linux systems the first user is `1000` / `1000`, which is
the default).

Everything else is optional; see [Settings](#settings).

### 3. Sign in to Yoto (once)

This opens Yoto's normal sign-in page and saves a refresh token to
`data/.refresh_token`. You only do it once.

```bash
docker compose run --rm -p 127.0.0.1:8787:8787 yoto-sleeptimer login
```

It prints a long `https://login.yotoplay.com/authorize?...` link. Open it
in a browser, sign in with your Yoto account and approve access. Yoto then
sends the browser back to `http://127.0.0.1:8787/callback`. How that last
hop reaches the service depends on where your browser is:

- **Browser on the same machine** (a desktop, laptop or Mac): nothing to
  do. The browser shows "Signed in" and the terminal finishes by itself.

- **Headless machine you reach over SSH** (a server or NAS). Pick either:
  - **Paste the address (simplest).** After you approve, your browser
    shows a "can't connect" / "site can't be reached" page. That's
    expected. Copy the whole address from the address bar (it starts with
    `http://127.0.0.1:8787/callback?code=`), paste it into the SSH
    terminal, and press Enter.
  - **Forward the port.** Connect with
    `ssh -L 8787:127.0.0.1:8787 you@your-server` and run the login
    command in that session. The browser's redirect then travels through
    SSH to the server and finishes automatically.

When it's done you'll see something like:

```
Signed in. Token saved to /data/.refresh_token (keep it private).
Players on this account: Bedroom, Playroom
```

The link is only valid for this one attempt. If it fails or times out, just
run the command again. To replace an existing token, add `--force`:
`docker compose run --rm -p 127.0.0.1:8787:8787 yoto-sleeptimer login --force`.

### 4. Start it

```bash
docker compose up -d --build
docker compose logs -f
```

You should see `Watching N player(s)` and then `Connected; waiting for
playback.` Press play on a player (with no sleep timer set) and within a
few seconds the log shows `Armed 90-min sleep timer on ...`. In the Yoto app
that player now has a sleep timer running.

It restarts automatically after a reboot or crash (`restart:
unless-stopped`), so you're done.

> **Trying it out first?** Set `DRY_RUN=1` in `.env` and it will log
> `[dry run] would arm ...` instead of setting any timers.

## Settings

All settings go in `.env`. After changing one, run
`docker compose up -d` to apply it.

| Setting | Default | What it does |
| --- | --- | --- |
| `YOTO_CLIENT_ID` | (required) | Client ID of your Yoto developer app. |
| `SLEEP_MINUTES` | `90` | Length of the automatic sleep timer, in minutes. |
| `PLAYERS` | all players | Only manage these players: comma-separated names (as shown in the Yoto app) or device ids, not case-sensitive. Example: `Bedroom, Playroom`. |
| `ACTIVE_HOURS` | any time | Only set timers inside this daily window, 24-hour clock, may wrap past midnight. Example: `18:00-07:00` for evenings and nights only, so daytime listening isn't cut off. |
| `TZ` | `UTC` | Your [time zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones), used for `ACTIVE_HOURS` and log times. |
| `DRY_RUN` | `0` | `1` logs what it would do without setting timers. |
| `PUID` / `PGID` | `1000` | Docker only: user and group the container runs as. Must own `data/`. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for much more detail while troubleshooting. |
| `REARM_COOLDOWN_SECONDS` | `120` | Minimum gap between two timers on the same player. |
| `RETRY_SECONDS` | `30` | Wait before reconnecting after the connection drops. |
| `CONNECT_TIMEOUT_SECONDS` | `180` | Give up on a stuck connection attempt after this long and retry. |
| `YOTO_REDIRECT_URI` | `http://127.0.0.1:8787/callback` | Login only. Must match the callback URL registered on your Yoto app. |
| `YOTO_SCOPES` | `openid profile offline_access` | Login only. OAuth scopes to request; add any extra scope names your Yoto app requires. |
| `DATA_DIR` | `./data` (`/data` in Docker) | Where the refresh token is kept. |

To check your settings without starting anything:

```bash
docker compose run --rm yoto-sleeptimer check
```

## Day-to-day

```bash
docker compose ps                    # is it running?
docker compose logs --tail 50        # recent activity
docker compose restart               # restart it
docker compose down                  # stop it
```

Logs are capped at 3 x 10 MB by Docker, so they can't fill an SD card.

**Updating:**

```bash
git pull
docker compose up -d --build
```

Your `.env` and `data/` are never touched by updates.

**Moving to another machine:** copy the whole folder, including `.env` and
`data/`, and run `docker compose up -d --build` there. Don't run two copies
at once: they'd both rotate the same refresh token and one would end up
signed out.

## Running without Docker

You need Python 3.10 or newer.

```bash
git clone https://github.com/ndurell/yoto-sleeptimer.git
cd yoto-sleeptimer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then set YOTO_CLIENT_ID and TZ
.venv/bin/python -m yoto_sleeptimer login
.venv/bin/python -m yoto_sleeptimer run
```

The program reads `.env` from the folder you run it in, and keeps the token
in `./data/.refresh_token`. `PUID` / `PGID` are ignored outside Docker.

To keep it running on Linux, use a systemd service, for example
`/etc/systemd/system/yoto-sleeptimer.service` (adjust the user and paths):

```ini
[Unit]
Description=Yoto automatic sleep timer
Wants=network-online.target
After=network-online.target

[Service]
User=youruser
WorkingDirectory=/home/youruser/yoto-sleeptimer
ExecStart=/home/youruser/yoto-sleeptimer/.venv/bin/python -m yoto_sleeptimer run
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now yoto-sleeptimer` and follow the logs with
`journalctl -u yoto-sleeptimer -f`.

## Adding it to an existing Docker Compose setup

If you already run other services from one `docker-compose.yml` on a
home server, you can add
this as one more service instead of running it from its own folder. Put
this repository in a subfolder, say `yoto-sleeptimer/`, next to your
compose file, and add:

```yaml
services:
  yoto-sleeptimer:
    build: ./yoto-sleeptimer
    image: yoto-sleeptimer:local
    container_name: yoto-sleeptimer
    restart: unless-stopped
    user: "${PUID:-1000}:${PGID:-1000}"
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    environment:
      TZ: "${TZ:-UTC}"
      YOTO_CLIENT_ID: "${YOTO_CLIENT_ID}"
      SLEEP_MINUTES: "90"
      # PLAYERS: "Bedroom"
      # ACTIVE_HOURS: "18:00-07:00"
    volumes:
      - ./yoto-data:/data   # holds the refresh token; keep it out of git
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```

Put `YOTO_CLIENT_ID` in that setup's `.env`, run `mkdir -p yoto-data`,
sign in once with
`docker compose run --rm -p 127.0.0.1:8787:8787 yoto-sleeptimer login`,
then `docker compose up -d --build yoto-sleeptimer`. It needs no published
ports and no host networking, so it won't clash with anything else.

Mount a **directory** at `/data`, not just the token file: the service
saves a rotated token by writing a new file and renaming it over the old
one, which doesn't work on a single-file bind mount.

## Moving from an older Yoto app (no scopes or device-code)

Yoto has changed how developer apps work. Two changes matter if you set up
a Yoto app (for this or any other script) a while ago:

- **Apps without scopes are being switched off.** Older apps were created
  before Yoto had scopes, so they list none. Several open-source Yoto
  projects report that client IDs with no scopes stop working after
  **30 November 2026**. Once that happens, an app with no scopes can't get
  new access tokens, so a watcher using it stops setting timers. It may
  look connected right up until it stops.
- **Device-code sign-in is deprecated.** Yoto now recommends browser
  sign-in with a local callback for command-line and headless apps, which
  is what this project's `login` uses. The old flow had you visit a page
  and type a short code. Tokens you already got that way keep refreshing,
  but you can't count on that flow for a new sign-in.

To check yours, open your app in the Yoto developer dashboard. If the
scopes list is empty, move to a new app:

1. **Create a new app** as in
   [step 1](#1-create-a-yoto-developer-app-get-a-client-id): public client,
   callback URL `http://127.0.0.1:8787/callback`, `offline_access` plus the
   device view / control scopes. Projects that have migrated report making
   a new app rather than adding scopes to the old one.
2. **Put the new client ID in `.env`** (`YOTO_CLIENT_ID=...`).
3. **Sign in again.** Your existing refresh token belongs to the old app,
   and adding scopes doesn't upgrade a token you already have:
   ```bash
   docker compose run --rm -p 127.0.0.1:8787:8787 yoto-sleeptimer login --force
   ```
4. **Restart:** `docker compose up -d`. The log should show
   `Watching N player(s)` and `Connected; waiting for playback.` again.
5. Once it's working, you can delete the old app in the dashboard.

**Running more than one copy?** Don't: stop any other watcher or script on
the same Yoto account before you start this one. Two would both set
timers, and two copies sharing one token would rotate it out from under
each other.

These dates and details come from other projects' migration notes, not an
official Yoto announcement. Check the Yoto developer docs at
[yoto.dev](https://yoto.dev) for the current rules.

## Troubleshooting

**`YOTO_CLIENT_ID is not set`**: `.env` is missing, or still has the
placeholder. See [step 2](#2-get-the-code-and-configure-it).

**`no refresh token at /data/.refresh_token`**: you haven't signed in yet
(or the token is in a different folder). Do [step 3](#3-sign-in-to-yoto-once).

**`Permission denied` writing to `/data`**: the `data` folder is owned by a
different user than `PUID`/`PGID`. This usually happens when Docker created
it as root because it didn't exist yet. Fix with
`sudo chown -R $(id -u):$(id -g) data` and check `PUID`/`PGID` in `.env`.

**The login page says the callback / redirect URL is not allowed**: the
callback URL on your Yoto app must be exactly
`http://127.0.0.1:8787/callback` (or whatever `YOTO_REDIRECT_URI` is set to).
`localhost` and `127.0.0.1` count as different.

**`Yoto did not return a refresh token`**: your app doesn't allow
offline access / refresh tokens. Enable it in the Yoto developer dashboard
and sign in again.

**The token stopped working (`Refresh token invalid` over and over)**: it
was revoked (for example you changed your Yoto password), expired after a
long time switched off, or a second copy of the service rotated it. Sign in
again with `login --force` and restart: `docker compose restart`.

**It worked for months and then stopped, around or after 30 November
2026**, or sign-in fails with an error about the client or scopes not being
allowed or pre-approved: your Yoto app probably has no scopes or is
missing `offline_access`. See
[Moving from an older Yoto app](#moving-from-an-older-yoto-app-no-scopes-or-device-code).

**`PLAYERS matched none of this account's players`**: the log line lists
the names it found. Use one of those exactly (case doesn't matter).

**It's connected but never arms a timer**:
- Is a sleep timer already running on the player? Then it's left alone, as
  designed.
- Is it outside `ACTIVE_HOURS`? Check `TZ` too, or times will be off by
  your UTC offset.
- Is `DRY_RUN=1`?
- Set `LOG_LEVEL=DEBUG`, `docker compose up -d`, press play, and look at
  `docker compose logs`.

**`Live connection dropped` / `timed out` now and then**: normal on home
internet; it reconnects by itself after `RETRY_SECONDS`. It's only a
problem if it never gets back to `Connected`.

## Security notes

- `data/.refresh_token` is effectively a password for your Yoto account.
  It's written with `0600` permissions and is gitignored, as is `.env`.
  Never commit or share them.
- The client ID isn't secret on its own, but there's no reason to publish
  it either.
- The container runs as an ordinary user with all Linux capabilities
  dropped, needs no inbound ports, and only talks to Yoto's servers. The
  login command briefly listens on `127.0.0.1:8787` for the sign-in
  callback and stops as soon as it's done.
- To revoke access completely, delete `data/.refresh_token` and remove the
  app's access from your Yoto account / developer dashboard.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
docker build -t yoto-sleeptimer:dev .
```

Layout:

```
yoto_sleeptimer/
  __main__.py     command line: run (default), login, check
  config.py       settings from environment variables / .env
  logic.py        pure decision logic: should_arm, active hours, player filter
  watcher.py      Yoto connection, event handling, reconnect loop
  login.py        browser sign-in (OAuth Authorization Code + PKCE)
  token_store.py  atomic, private refresh-token file
tests/            unit tests; no network, no Yoto account needed
```

The decision of *when* to set a timer lives in `logic.py` and has no
network code, so it's easy to change and test. GitHub Actions runs the
tests and builds the image for `amd64` and `arm64` (ARM boards, Apple Silicon) on every
pull request.

Library versions are pinned in `requirements.txt`. Yoto's API changes from
time to time; if things break after an upgrade of `yoto_api`, the tests
under `tests/` and a `DRY_RUN=1` run are the quickest way to check.

Issues and pull requests are welcome.

## License

[MIT](LICENSE)
