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
    )
    resp.raise_for_status()
    data = resp.json()
    interaction_id = data.get("interactionId")
    interaction_token = data.get("interactionToken")
    node_id = data.get("id")

    # Step 2b — FPL added a "Protect SDK" probe node before the login form.
    # The start node has interactionId + id but no interactionToken.
    # Submitting an empty protectsdk payload advances to the actual login node.
    if not interaction_token and interaction_id and node_id and data.get("connectionId"):
        probe_resp = session.post(
            f"{_AUTH_BASE}/davinci/connections/{data['connectionId']}/capabilities/{data.get('capabilityName', 'customHTMLTemplate')}",
            headers={"interactionId": interaction_id, "Content-Type": "application/json"},
            json={"id": node_id, "eventName": "continue", "parameters": {"protectsdk": ""}},
        )
        probe_resp.raise_for_status()
        probe_data = probe_resp.json()
        interaction_token = probe_data.get("interactionToken")
        node_id = probe_data.get("id", node_id)

    if not interaction_token or not interaction_id or not node_id:
        raise RuntimeError(
            f"DaVinci login failed — could not get interactionToken after protect-SDK probe. "
            f"Start response keys: {list(data.keys())}. FPL auth flow may have changed."
        )

    conn_url = f"{_AUTH_BASE}/davinci/connections/{_CONN_ID}/capabilities/customHTMLTemplate"
    auth_headers = {"interactionId": interaction_id, "interactionToken": interaction_token}

    # Step 3a — Polling probe
    resp = session.post(
        conn_url,
        headers=auth_headers,
        json={
            "id": node_id,
            "eventName": "continue",
            "parameters": {"eventType": "polling"},
            "pollProps": {
                "status": "continue",
                "delayInMs": 10,
                "retriesAllowed": 1,
                "pollChallengeStatus": False,
            },
        },
    )
    resp.raise_for_status()
    node_id = resp.json()["id"]

    # Step 3b — Submit credentials
    resp = session.post(
        conn_url,
        headers=auth_headers,
        json={
            "id": node_id,
            "nextEvent": {
                "constructType": "skEvent",
                "eventName": "continue",
                "params": [],
                "eventType": "post",
                "postProcess": {},
            },
            "parameters": {
                "buttonType": "form-submit",
                "buttonValue": "SIGNON",
                "username": email,
                "password": password,
            },
            "eventName": "continue",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    if "dvResponse" not in body:
        raise RuntimeError(f"Login failed — check credentials. Response: {body}")
    dv_response = body["dvResponse"]

    # Step 4 — Resume OAuth flow
    resp = session.post(
        f"{_AUTH_BASE}/as/resume",
        data={"dvResponse": dv_response, "state": form_state},
        allow_redirects=False,
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
    )
    resp.raise_for_status()
    token_data = resp.json()
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
