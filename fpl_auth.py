"""
FPL OAuth2 PKCE login.
Based on the updated flow from amosbastian/fpl PR #135 (Aug 2025).
The old POST-to-users.premierleague.com flow is deprecated.
"""
import base64
import hashlib
import os
import re
import secrets
import uuid

import requests

_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chrome-profile")

_AUTH_BASE = "https://account.premierleague.com"
_CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"
_REDIRECT_URI = "https://fantasy.premierleague.com/"
_POLICY_ID = "262ce4b01d19dd9d385d26bddb4297b6"
_CONN_ID = "0d8c928e4970386733ce110b9dda8412"

#: Every auth call gets an explicit timeout. A hung socket here would block
#: the whole CI job until its 30-minute cap with no alert raised.
_TIMEOUT = 30


def _verifier():
    return secrets.token_urlsafe(64)[:128]


def _challenge(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def login(email: str, password: str) -> tuple[str, requests.Session]:
    """
    Returns (access_token, requests.Session) on success.
    Raises on bad credentials or auth failure.
    """
    verifier = _verifier()
    state = uuid.uuid4().hex
    session = requests.Session()

    # Step 1 — OAuth2 auth page: get embedded access_token + form state
    r = session.get(
        f"{_AUTH_BASE}/as/authorize",
        params={
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile email offline_access",
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    html = r.text

    m = re.search(r'"accessToken":"([^"]+)"', html)
    if not m:
        raise RuntimeError("Could not find accessToken in FPL auth page. The auth flow may have changed.")
    page_token = m.group(1)

    m = re.search(r'<input[^>]+name="state"[^>]+value="([^"]+)"', html)
    if not m:
        raise RuntimeError("Could not find state field in FPL auth page.")
    form_state = m.group(1)

    # Step 2 — Start DaVinci interaction
    resp = session.post(
        f"{_AUTH_BASE}/davinci/policy/{_POLICY_ID}/start",
        headers={"Authorization": f"Bearer {page_token}", "Content-Type": "application/json"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    interaction_id = data.get("interactionId")
    node_id = data.get("id")

    if not interaction_id or not node_id:
        raise RuntimeError(
            f"DaVinci login failed — no interactionId/id in start response. "
            f"Keys: {list(data.keys())}. FPL auth flow may have changed."
        )

    # Step 3 — Walk the DaVinci node chain until the flow returns dvResponse.
    #
    # FPL now puts several nodes in front of (and behind) the login form:
    #   node 0  Protect-SDK / polling probe   -> wants {"protectsdk": ""}
    #   node 1  the actual login form         -> wants username + password
    #   node 2  "Logging you in..." auto-post -> wants just the submit button
    # There is NO interactionToken until the final node; the interaction is
    # tracked by the interactionId header plus the cookie the session holds.
    # Each node also names its own connectionId/capabilityName, so those are
    # read fresh from the current node rather than from a hardcoded constant.
    node = data
    body = None
    for step in range(6):
        screen_props = node.get("screen", {}).get("properties", {})
        fields = [
            f.get("propertyName")
            for f in screen_props.get("formFieldsList", {}).get("value", [])
        ]
        print(
            f"[fpl_auth] davinci step {step + 1}: node={node.get('id')} "
            f"cap={node.get('capabilityName')} fields={fields} keys={list(node.keys())}"
        )

        # Pick the payload from the fields the node actually declares.
        if "protectsdk" in fields:
            params = {"protectsdk": ""}
        elif "password" in fields:
            params = {
                "buttonType": "form-submit",
                "buttonValue": "SIGNON",
                "username": email,
                "password": password,
            }
        else:
            params = {"buttonType": "form-submit", "buttonValue": "SIGNON"}

        resp = session.post(
            f"{_AUTH_BASE}/davinci/connections/{node['connectionId']}"
            f"/capabilities/{node['capabilityName']}",
            headers={"interactionId": interaction_id, "Content-Type": "application/json"},
            json={
                "id": node["id"],
                "nextEvent": {
                    "constructType": "skEvent",
                    "eventName": "continue",
                    "params": [],
                    "eventType": "post",
                    "postProcess": {},
                },
                "parameters": params,
                "eventName": "continue",
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 400:
            # DaVinci reports bad credentials as HTTP 400 with the reason in the
            # body, so parse it before raise_for_status() turns it into an
            # opaque HTTPError.
            print(f"[fpl_auth] davinci step {step + 1} HTTP {resp.status_code}: {resp.text[:600]}")
            try:
                err = resp.json()
            except ValueError:
                err = {}
            if err.get("connectorId") == "errorConnector" or err.get("error_reason"):
                raise RuntimeError(
                    f"Login failed — check credentials. DaVinci error: "
                    f"{err.get('error_reason') or err.get('message')}"
                )
        resp.raise_for_status()
        node = resp.json()

        if "dvResponse" in node:
            body = node
            break

    if body is None:
        raise RuntimeError(
            f"DaVinci login failed — no dvResponse after walking the node chain. "
            f"Last node keys: {list(node.keys())}, id={node.get('id')}, "
            f"connectionId={node.get('connectionId')}. FPL auth flow may have changed."
        )
    dv_response = body["dvResponse"]

    # Step 4 — Resume OAuth flow
    resp = session.post(
        f"{_AUTH_BASE}/as/resume",
        data={"dvResponse": dv_response, "state": form_state},
        allow_redirects=False,
        timeout=_TIMEOUT,
    )
    location = resp.headers.get("Location", "")
    m = re.search(r"[?&]code=([^&]+)", location)
    if not m:
        raise RuntimeError(f"No auth code in redirect. Location: {location}")
    auth_code = m.group(1)

    # Step 5 — Exchange code for access token
    resp = session.post(
        f"{_AUTH_BASE}/as/token",
        data={
            "grant_type": "authorization_code",
            "redirect_uri": _REDIRECT_URI,
            "code": auth_code,
            "code_verifier": verifier,
            "client_id": _CLIENT_ID,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    token_data = resp.json()
    if token_data.get("refresh_token"):
        _last_refresh_token["value"] = token_data["refresh_token"]
    return token_data["access_token"], session


def login_browser() -> tuple[str, requests.Session]:
    """
    Opens a separate Chrome window (isolated from the user's Chrome) for login.
    Captures the OAuth code from the redirect URL, then closes the window.
    The profile is persistent so Google login is remembered next time.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Browser login requires Playwright.\n"
            "Run: pip install playwright && python -m playwright install chromium"
        )
    from urllib.parse import urlencode, unquote
    import time

    verifier = _verifier()
    state = uuid.uuid4().hex
    auth_url = f"{_AUTH_BASE}/as/authorize?" + urlencode({
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": "openid profile email offline_access",
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    })

    captured = []

    def _grab(url: str):
        if not captured and "fantasy.premierleague.com" in url and "code=" in url:
            m = re.search(r"[?&]code=([^&]+)", url)
            if m:
                captured.append(unquote(m.group(1)))

    with sync_playwright() as pw:
        # Use our own isolated profile with the real Chrome binary.
        # channel="chrome" passes Google's "insecure browser" check.
        # We never touch the user's existing Chrome profile or tabs.
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=_PROFILE_DIR,
                channel="chrome",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            # Chrome not installed; fall back to Playwright's Chromium.
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=_PROFILE_DIR,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )

        # Reuse an existing tab or open a fresh one
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Capture method 1: framenavigated fires before the SPA strips ?code= from URL
        page.on("framenavigated", lambda frame: _grab(frame.url) if not frame.parent_frame else None)

        # Capture method 2: route interception catches the redirect at network level
        def on_route(route):
            _grab(route.request.url)
            try:
                route.continue_()
            except Exception:
                pass

        ctx.route(re.compile(r"https://fantasy\.premierleague\.com"), on_route)

        page.goto(auth_url)

        deadline = time.time() + 300
        while not captured:
            if time.time() > deadline:
                break
            try:
                page.wait_for_timeout(500)
            except Exception:
                break

        try:
            ctx.close()
        except Exception:
            pass

    if not captured:
        raise RuntimeError("Login cancelled or timed out (5 min).")

    session = requests.Session()
    resp = session.post(
        f"{_AUTH_BASE}/as/token",
        data={
            "grant_type": "authorization_code",
            "redirect_uri": _REDIRECT_URI,
            "code": captured[0],
            "code_verifier": verifier,
            "client_id": _CLIENT_ID,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    token_data = resp.json()
    # Store full token response and session cookies for GitHub Actions use
    _last_refresh_token["value"] = token_data.get("refresh_token", "")
    _last_refresh_token["all_fields"] = list(token_data.keys())
    # Save session cookies as a fallback (works even if no refresh_token issued)
    _last_refresh_token["cookies"] = dict(session.cookies)
    return token_data["access_token"], session


# Holds the refresh token from the most recent login_browser() call.
# app.py reads this immediately after login to persist it.
_last_refresh_token: dict = {"value": ""}


def refresh_login(refresh_token: str) -> tuple[str, requests.Session]:
    """
    Exchange a saved refresh token for a new access token.
    Works for Google-linked FPL accounts — no browser or password needed.
    Refresh tokens typically last 30-60 days before needing a new browser login.
    """
    session = requests.Session()
    resp = session.post(
        f"{_AUTH_BASE}/as/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CLIENT_ID,
            "scope": "openid profile email offline_access",
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code == 400:
        raise RuntimeError(
            "Refresh token expired or invalid. Log in via the web app again "
            "to get a new one, then update the FPL_REFRESH_TOKEN secret."
        )
    resp.raise_for_status()
    token_data = resp.json()
    # Update stored refresh token if a new one was issued
    if token_data.get("refresh_token"):
        _last_refresh_token["value"] = token_data["refresh_token"]
    return token_data["access_token"], session
