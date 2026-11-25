"""One-time sign-in, then save the refresh token.

Uses OAuth Authorization Code + PKCE with a loopback callback, which is what
Yoto recommends for command-line and headless apps (the older device-code
flow is deprecated). The browser can be on a different machine from this
program: either forward the callback port over SSH, or copy the address the
browser lands on and paste it here.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import sys
import threading
import urllib.parse

import aiohttp
from aiohttp import web

from . import token_store
from .config import Config

AUTHORIZE_URL = "https://login.yotoplay.com/authorize"
TOKEN_URL = "https://login.yotoplay.com/oauth/token"
AUDIENCE = "https://api.yotoplay.com"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8787/callback"
# Yoto only grants the scopes requested here (ticking them on the app is not
# enough), and the watcher must list players and send them commands.
DEFAULT_SCOPES = "openid profile offline_access family:devices:view family:devices:control"

_DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>Signed in</title>
<body style="font-family:sans-serif;max-width:32em;margin:4em auto">
<h1>{title}</h1><p>{body}</p></body>"""


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_authorize_url(client_id: str, redirect_uri: str, scopes: str,
                        challenge: str, state: str) -> str:
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "audience": AUDIENCE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{query}"


def parse_callback(params: dict, expected_state: str) -> str:
    """Return the authorization code from callback query parameters."""
    if params.get("error"):
        raise RuntimeError(
            f"Yoto refused the sign-in: {params.get('error_description') or params['error']}")
    if params.get("state") != expected_state:
        raise RuntimeError("sign-in response did not match this login attempt (state mismatch)")
    code = params.get("code")
    if not code:
        raise RuntimeError("no authorization code in the sign-in response")
    return code


async def _wait_for_code(redirect_uri: str, state: str) -> str:
    """Resolve with the code from whichever comes first: the browser hitting the
    local callback, or the user pasting the callback address into the terminal."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[str] = loop.create_future()
    parsed = urllib.parse.urlparse(redirect_uri)

    def settle(params: dict) -> bool:
        if result.done():
            return True
        try:
            result.set_result(parse_callback(params, state))
            return True
        except RuntimeError as exc:
            result.set_exception(exc)
            return False

    async def callback(request: web.Request) -> web.Response:
        ok = settle(dict(request.query))
        page = _DONE_PAGE.format(
            title="Signed in" if ok else "Sign-in failed",
            body="You can close this tab and go back to the terminal." if ok
            else "Go back to the terminal for details.")
        return web.Response(text=page, content_type="text/html")

    app = web.Application()
    app.router.add_get(parsed.path or "/", callback)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    bind = os.environ.get("LOGIN_BIND", "127.0.0.1")
    port = parsed.port or 80
    try:
        site = web.TCPSite(runner, bind, port)
        await site.start()
    except OSError as exc:
        print(f"(Could not listen on {bind}:{port}: {exc}. Use the paste option.)")

    def on_paste(line: str) -> None:
        if result.done():
            return
        line = line.strip()
        if not line:
            return
        query = urllib.parse.urlparse(line).query or line
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        if "code" not in params and "error" not in params:
            print("That doesn't look like the callback address "
                  "(it should contain ?code=...). Try again.")
            return
        settle(params)

    def read_paste() -> None:
        # A daemon thread, not the event loop's executor: a blocked readline
        # can't be cancelled, and asyncio.run() would wait for an executor
        # worker at shutdown, so login would hang until Enter was pressed
        # after the browser callback (or the timeout) had already finished.
        # A daemon thread is simply abandoned when the program exits.
        while True:
            try:
                line = sys.stdin.readline()
            except (OSError, ValueError):
                return
            if not line:
                return  # no terminal attached; rely on the callback
            try:
                loop.call_soon_threadsafe(on_paste, line)
            except RuntimeError:
                return  # event loop already closed

    threading.Thread(target=read_paste, name="login-paste", daemon=True).start()
    try:
        return await asyncio.wait_for(result, timeout=600)
    finally:
        await runner.cleanup()


async def _exchange(session: aiohttp.ClientSession, client_id: str, code: str,
                    verifier: str, redirect_uri: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    async with session.post(TOKEN_URL, data=data) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200 or body.get("error"):
            raise RuntimeError(
                f"token exchange failed ({resp.status}): "
                f"{body.get('error_description') or body.get('error') or body}")
        return body


async def login(cfg: Config, force: bool = False) -> int:
    from yoto_api import YotoClient

    if cfg.token_file.exists() and cfg.token_file.read_text().strip() and not force:
        print(f"A refresh token already exists at {cfg.token_file}.")
        print("Run login again with --force to replace it.")
        return 1

    redirect_uri = os.environ.get("YOTO_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI
    scopes = os.environ.get("YOTO_SCOPES", "").strip() or DEFAULT_SCOPES
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    url = build_authorize_url(cfg.client_id, redirect_uri, scopes, challenge, state)

    print()
    print("1. Open this link in a browser and sign in with your Yoto account:")
    print()
    print(f"   {url}")
    print()
    print(f"2. Yoto then sends the browser to {redirect_uri}.")
    print("   - Browser on this machine, or the port forwarded over SSH:")
    print("     it finishes by itself.")
    print("   - Otherwise the browser shows a \"can't connect\" page. That's fine:")
    print("     copy the full address from its address bar and paste it here.")
    print()
    print("Waiting (10 minutes max)...")

    try:
        code = await _wait_for_code(redirect_uri, state)
    except asyncio.TimeoutError:
        print("Timed out. Run login again.")
        return 1
    except RuntimeError as exc:
        print(f"Sign-in failed: {exc}")
        return 1

    async with aiohttp.ClientSession() as session:
        try:
            body = await _exchange(session, cfg.client_id, code, verifier, redirect_uri)
        except RuntimeError as exc:
            print(f"Sign-in failed: {exc}")
            return 1
        refresh_token = body.get("refresh_token")
        if not refresh_token:
            print("Yoto did not return a refresh token. Check that your app allows "
                  "offline_access / refresh tokens (see the README).")
            return 1
        token_store.write(cfg.token_file, refresh_token)

        names: list[str] = []
        try:
            async with YotoClient(client_id=cfg.client_id, session=session,
                                  refresh_hook=lambda t: token_store.write(
                                      cfg.token_file, t.refresh_token)) as client:
                client.set_refresh_token(refresh_token)
                await client.refresh()
                await client.update_player_list()
                names = [p.device.name for p in client.players.values()]
        except Exception as exc:  # the token is saved; this is just a friendly check
            print(f"(Saved the token, but listing players failed: {exc})")

    print()
    print(f"Signed in. Token saved to {cfg.token_file} (keep it private).")
    if names:
        print(f"Players on this account: {', '.join(names)}")
    return 0
