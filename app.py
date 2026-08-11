"""FPL Auto — local Flask web app for managing your Fantasy Premier League team."""
import json
import os
import threading

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import fpl_api
import fpl_auth

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", os.urandom(24).hex())

# In-memory state (single user, local tool)
_state = {
    "token": None,
    "session": None,
    "entry_id": None,
    "bootstrap": None,  # cached on login
}

# Browser-login async state (Playwright blocks a thread)
_browser_login = {"status": "idle", "error": None}

SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session.json")


def _save_session(refresh_token: str = None, cookies: dict = None):
    data = {"token": _state["token"], "entry_id": _state["entry_id"]}
    # Preserve existing saved fields if not overwriting
    if os.path.exists(SESSION_FILE):
        try:
            existing = json.load(open(SESSION_FILE))
            if existing.get("refresh_token") and not refresh_token:
                data["refresh_token"] = existing["refresh_token"]
            if existing.get("session_cookies") and not cookies:
                data["session_cookies"] = existing["session_cookies"]
        except Exception:
            pass
    if refresh_token:
        data["refresh_token"] = refresh_token
    if cookies:
        data["session_cookies"] = cookies
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f)


def _load_session():
    if not os.path.exists(SESSION_FILE):
        return
    try:
        with open(SESSION_FILE) as f:
            data = json.load(f)
        _state["token"] = data.get("token")
        _state["entry_id"] = data.get("entry_id")
    except Exception:
        pass


def _get_saved_refresh_token() -> str:
    if not os.path.exists(SESSION_FILE):
        return ""
    try:
        return json.load(open(SESSION_FILE)).get("refresh_token", "")
    except Exception:
        return ""


def _enrich_picks(picks: list, bootstrap: dict) -> list:
    """Add player name, team, position, and price to each pick dict."""
    players_by_id = {p["id"]: p for p in bootstrap["elements"]}
    teams_by_id = {t["id"]: t for t in bootstrap["teams"]}
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    for pick in picks:
        p = players_by_id.get(pick.get("element"), {})
        pick["name"] = f"{p.get('first_name', '')} {p.get('second_name', '')}".strip()
        pick["web_name"] = p.get("web_name", pick["name"])
        t = teams_by_id.get(p.get("team"), {})
        pick["team"] = t.get("short_name") or t.get("name", "")
        pick["team_code"] = t.get("code", 0)
        pick["pos"] = pos_map.get(p.get("element_type"), "")
        pick["price"] = p.get("now_cost", 0) / 10
        pick["photo"] = p.get("photo", "").replace(".jpg", "")
    return picks


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/login", methods=["POST"])
def do_login():
    data = request.get_json(force=True)
    email = data.get("email", "").strip()
    password = data.get("password", "")
    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password are required."}), 400

    try:
        token, session = fpl_auth.login(email, password)
        _state["token"] = token
        _state["session"] = session

        user = fpl_api.me(session, token)
        _state["entry_id"] = user["player"].get("entry")

        # Cache bootstrap (all players + teams)
        _state["bootstrap"] = fpl_api.bootstrap(session)

        _save_session()
        return jsonify({
            "ok": True,
            "entry_id": _state["entry_id"],
            "name": f"{user['player'].get('first_name', '')} {user['player'].get('last_name', '')}".strip(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 401


@app.route("/api/status")
def status():
    if not _state["token"]:
        return jsonify({"logged_in": False})
    try:
        # Re-use cached session if available, else create a new one
        session = _state["session"]
        if session is None:
            import requests as req
            session = req.Session()
            _state["session"] = session

        user = fpl_api.me(session, _state["token"])
        entry_id = _state["entry_id"] or user["player"].get("entry")
        _state["entry_id"] = entry_id

        if not _state["bootstrap"]:
            _state["bootstrap"] = fpl_api.bootstrap(session)

        return jsonify({
            "logged_in": True,
            "entry_id": entry_id,
            "has_team": entry_id is not None,
            "name": f"{user['player'].get('first_name', '')} {user['player'].get('last_name', '')}".strip(),
        })
    except Exception:
        return jsonify({"logged_in": False})


@app.route("/api/bootstrap")
def get_bootstrap():
    """Returns all players and teams (fetches fresh if needed)."""
    session = _state["session"]
    if session is None:
        import requests as req
        session = req.Session()
        _state["session"] = session

    if not _state["bootstrap"]:
        _state["bootstrap"] = fpl_api.bootstrap(session)

    bs = _state["bootstrap"]
    teams_by_id = {t["id"]: t["name"] for t in bs["teams"]}
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

    players = [
        {
            "id": p["id"],
            "name": f"{p['first_name']} {p['second_name']}",
            "web_name": p.get("web_name", ""),
            "team": teams_by_id.get(p["team"], ""),
            "position": pos_map.get(p["element_type"], ""),
            "price": p["now_cost"] / 10,
            "status": p.get("status", "a"),
            "total_points": p.get("total_points", 0),
        }
        for p in bs["elements"]
    ]
    return jsonify(players)


@app.route("/api/players")
def search_players():
    q = request.args.get("q", "").lower().strip()
    pos = request.args.get("pos", "").upper()

    if not _state["bootstrap"]:
        if _state["session"]:
            _state["bootstrap"] = fpl_api.bootstrap(_state["session"])
        else:
            return jsonify([])

    bs = _state["bootstrap"]
    teams_by_id = {t["id"]: t["name"] for t in bs["teams"]}
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    pos_filter = {v: k for k, v in pos_map.items()}

    results = []
    for p in bs["elements"]:
        if pos and p["element_type"] != pos_filter.get(pos):
            continue
        name = f"{p['first_name']} {p['second_name']}".lower()
        web_name = p.get("web_name", "").lower()
        team = teams_by_id.get(p["team"], "").lower()
        if q and q not in name and q not in web_name and q not in team:
            continue
        results.append({
            "id": p["id"],
            "name": f"{p['first_name']} {p['second_name']}",
            "web_name": p.get("web_name", ""),
            "team": teams_by_id.get(p["team"], ""),
            "position": pos_map.get(p["element_type"], ""),
            "price": p["now_cost"] / 10,
            "status": p.get("status", "a"),
            "total_points": p.get("total_points", 0),
        })
    return jsonify(results[:60])


@app.route("/api/team")
def get_team():
    if not _state["token"]:
        return jsonify({"error": "Not logged in"}), 401

    # Re-fetch entry_id from the FPL API if not cached (e.g. team created after login)
    if not _state["entry_id"]:
        try:
            if not _state["session"]:
                import requests as req
                _state["session"] = req.Session()
            user = fpl_api.me(_state["session"], _state["token"])
            _state["entry_id"] = user["player"].get("entry")
            if _state["entry_id"]:
                _save_session()
        except Exception:
            pass

    if not _state["entry_id"]:
        return jsonify({"error": "No FPL team found. Please create one on fantasy.premierleague.com first."}), 404

    try:
        team_data = fpl_api.my_team(_state["session"], _state["token"], _state["entry_id"])
        if _state["bootstrap"] and team_data.get("picks"):
            team_data["picks"] = _enrich_picks(team_data["picks"], _state["bootstrap"])

        # Detect pre-season (no gameweek has started yet → unlimited free transfers)
        events = (_state["bootstrap"] or {}).get("events", [])
        team_data["is_preseason"] = not any(e.get("is_current") or e.get("finished") for e in events)

        # Add entry info: team name, overall rank, total points
        try:
            ei = fpl_api.entry_info(_state["session"], _state["token"], _state["entry_id"])
            team_data["entry_info"] = {
                "team_name": ei.get("name", ""),
                "rank": ei.get("summary_overall_rank"),
                "total_points": ei.get("summary_overall_points", 0),
            }
        except Exception:
            team_data["entry_info"] = {}

        return jsonify(team_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug/me")
def debug_me():
    """Temporary debug endpoint — shows raw /api/me/ response and app state."""
    if not _state["token"]:
        return jsonify({"error": "Not logged in"})
    try:
        if not _state["session"]:
            import requests as req
            _state["session"] = req.Session()
        raw = fpl_api.me(_state["session"], _state["token"])
        return jsonify({
            "me_response": raw,
            "state_entry_id": _state["entry_id"],
            "state_has_token": bool(_state["token"]),
        })
    except Exception as e:
        return jsonify({"error": str(e), "state_entry_id": _state["entry_id"]})


@app.route("/api/transfer", methods=["POST"])
def do_transfer():
    if not _state["token"]:
        return jsonify({"error": "Not logged in"}), 401
    if not _state["entry_id"]:
        return jsonify({"error": "No FPL team found"}), 404

    data = request.get_json(force=True)
    element_in = data.get("element_in")
    element_out = data.get("element_out")  # None for initial squad adds

    if not element_in:
        return jsonify({"error": "element_in is required"}), 400

    try:
        bs = _state["bootstrap"]
        players_by_id = {p["id"]: p for p in bs["elements"]}
        p_in = players_by_id.get(element_in)
        if not p_in:
            return jsonify({"error": "Player not found"}), 400

        purchase_price = p_in["now_cost"]

        # Get selling price from current squad if swapping
        selling_price = None
        if element_out:
            team_data = fpl_api.my_team(_state["session"], _state["token"], _state["entry_id"])
            for pick in team_data.get("picks", []):
                if pick["element"] == element_out:
                    selling_price = pick["selling_price"]
                    break
            if selling_price is None:
                return jsonify({"error": "Player to sell not found in your squad"}), 400

        # Determine current gameweek
        events = bs.get("events", [])
        next_gw = next((e["id"] for e in events if e.get("is_next")), None)
        current_gw = next((e["id"] for e in events if e.get("is_current")), None)
        event = next_gw or current_gw or 1

        transfer_payload = {"element_in": element_in, "purchase_price": purchase_price}
        if element_out:
            transfer_payload["element_out"] = element_out
            transfer_payload["selling_price"] = selling_price

        result = fpl_api.transfer(
            _state["session"], _state["token"], _state["entry_id"],
            event, [transfer_payload], chip=data.get("chip"),
        )
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/picks", methods=["POST"])
def do_picks():
    """Update captain, vice captain, and starting XI positions."""
    if not _state["token"]:
        return jsonify({"error": "Not logged in"}), 401
    if not _state["entry_id"]:
        return jsonify({"error": "No FPL team found"}), 404

    data = request.get_json(force=True)
    picks = data.get("picks")
    if not picks:
        return jsonify({"error": "picks array is required"}), 400

    try:
        result = fpl_api.update_picks(
            _state["session"], _state["token"], _state["entry_id"],
            picks, data.get("chips"),
        )
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _run_browser_login(update_session: bool = True):
    """Shared logic for browser login and token renewal."""
    _browser_login["status"] = "pending"
    _browser_login["error"] = None
    try:
        token, session = fpl_auth.login_browser()
        if update_session:
            _state["token"] = token
            _state["session"] = session
            user = fpl_api.me(session, token)
            _state["entry_id"] = user["player"].get("entry")
            _state["bootstrap"] = fpl_api.bootstrap(session)
        refresh_token = fpl_auth._last_refresh_token.get("value", "")
        cookies = fpl_auth._last_refresh_token.get("cookies", {})
        _save_session(refresh_token=refresh_token, cookies=cookies)
        _browser_login["status"] = "done"
    except Exception as e:
        _browser_login["status"] = "error"
        _browser_login["error"] = str(e)


@app.route("/api/login/browser", methods=["POST"])
def browser_login_start():
    """Kicks off a background thread that opens a Chromium browser for login."""
    if _browser_login["status"] == "pending":
        return jsonify({"status": "pending"})
    threading.Thread(target=_run_browser_login, kwargs={"update_session": True}, daemon=True).start()
    return jsonify({"status": "pending"})


@app.route("/api/login/browser/renew", methods=["POST"])
def browser_renew_token():
    """Re-runs browser login just to capture a fresh refresh token. Keeps current session."""
    if _browser_login["status"] == "pending":
        return jsonify({"status": "pending"})
    threading.Thread(target=_run_browser_login, kwargs={"update_session": False}, daemon=True).start()
    return jsonify({"status": "pending"})


@app.route("/api/login/browser/poll")
def browser_login_poll():
    """Frontend polls this until status is 'done' or 'error'."""
    status = _browser_login["status"]
    if status == "done":
        _browser_login["status"] = "idle"
        try:
            user = fpl_api.me(_state["session"], _state["token"])
            name = f"{user['player'].get('first_name', '')} {user['player'].get('last_name', '')}".strip()
            return jsonify({"status": "done", "name": name, "entry_id": _state["entry_id"]})
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)})
    return jsonify({"status": status, "error": _browser_login.get("error")})


@app.route("/api/entry")
def get_entry():
    if not _state["token"] or not _state["entry_id"]:
        return jsonify({"error": "Not logged in or no team"}), 401
    try:
        info = fpl_api.entry_info(_state["session"], _state["token"], _state["entry_id"])
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/refresh-token")
def get_refresh_token():
    """Returns saved auth data for GitHub Actions setup."""
    if not os.path.exists(SESSION_FILE):
        return jsonify({"error": "Not logged in yet — use the Sign in with Google button first."}), 404
    try:
        data = json.load(open(SESSION_FILE))
    except Exception:
        return jsonify({"error": "Session file unreadable."}), 500

    refresh_token = data.get("refresh_token", "")
    cookies = data.get("session_cookies", {})

    # Also check what was returned in the last in-memory browser login
    in_memory_fields = fpl_auth._last_refresh_token.get("all_fields", [])
    in_memory_rt = fpl_auth._last_refresh_token.get("value", "")
    in_memory_cookies = fpl_auth._last_refresh_token.get("cookies", {})

    if not refresh_token and not cookies and not in_memory_rt and not in_memory_cookies:
        return jsonify({
            "error": "No token captured yet. Click the 🔑 Renew token button and sign in with Google.",
            "debug_last_login_fields": in_memory_fields,
            "hint": "If debug_last_login_fields is empty, the browser login hasn't run in this server session yet.",
        }), 404

    return jsonify({
        "refresh_token": refresh_token or in_memory_rt or None,
        "session_cookies": cookies or in_memory_cookies or {},
        "oauth_fields_returned": in_memory_fields,
        "instructions": (
            "Copy 'refresh_token' to GitHub Secret FPL_REFRESH_TOKEN. "
            "If refresh_token is null, the OAuth server didn't issue one — use session_cookies instead "
            "(add as FPL_SESSION_COOKIES secret, update scripts to restore cookies)."
        ),
    })


@app.route("/api/predict")
def get_predict():
    squad_file = os.path.join(os.path.dirname(__file__), "research", "gw1_squad_2026.json")
    if not os.path.exists(squad_file):
        return jsonify({"error": "No prediction data available yet"}), 404
    with open(squad_file, encoding="utf-8") as f:
        squad = json.load(f)
    return jsonify({
        "gw": squad.get("gameweek"),
        "season": squad.get("season"),
        "captain": squad.get("captain_name"),
        "vice_captain": squad.get("vice_captain_name"),
        "squad_cost": squad.get("squad_cost_gpm"),
        "expected_xi_pts": squad.get("expected_xi_pts"),
        "squad": squad.get("squad_detail", []),
    })


@app.route("/api/analysis")
def get_analysis():
    base = os.path.dirname(__file__)
    docs = []
    candidates = [
        ("Season Plan (Latest)", os.path.join(base, "reports", "season_plan_latest.md")),
        ("GW Reflection (Latest)", os.path.join(base, "reports", "reflect_latest.md")),
        ("GW1 2026/27 Research Report", os.path.join(base, "research", "gw1_final_2026.md")),
    ]
    for title, path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                docs.append({"title": title, "content": f.read()})
    return jsonify(docs)


@app.route("/api/reflect")
def get_reflect():
    import requests as req

    entry_id = _state.get("entry_id")
    if not entry_id:
        return jsonify({"error": "Not logged in"}), 401

    gw = request.args.get("gw", type=int)
    if not gw:
        bs = _state.get("bootstrap")
        if not bs:
            session = _state.get("session")
            if not session:
                session = req.Session()
            try:
                bs = fpl_api.bootstrap(session)
            except Exception:
                bs = {}
        finished = [e for e in bs.get("events", []) if e.get("finished")]
        if not finished:
            return jsonify({"error": "No gameweeks have finished yet"}), 404
        gw = finished[-1]["id"]

    try:
        live_r = req.get(
            f"https://fantasy.premierleague.com/api/event/{gw}/live/",
            timeout=15, headers={"User-Agent": "fpl-auto"},
        )
        live_r.raise_for_status()
        picks_r = req.get(
            f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gw}/picks/",
            timeout=15, headers={"User-Agent": "fpl-auto"},
        )
        picks_r.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"FPL API error: {e}"}), 502

    live_data = live_r.json()
    picks_data = picks_r.json()

    bs = _state.get("bootstrap") or {}
    players_by_id = {p["id"]: p for p in bs.get("elements", [])}
    live_by_id = {e["id"]: e.get("stats", {}) for e in live_data.get("elements", [])}

    predicted_by_element = {}
    squad_file = os.path.join(os.path.dirname(__file__), "research", "gw1_squad_2026.json")
    if os.path.exists(squad_file):
        with open(squad_file, encoding="utf-8") as f:
            sq = json.load(f)
        for p in sq.get("squad_detail", []):
            predicted_by_element[p["element"]] = p.get("blended_xpts")

    results = []
    for pick in picks_data.get("picks", []):
        el_id = pick["element"]
        p_data = players_by_id.get(el_id, {})
        stats = live_by_id.get(el_id, {})
        actual_pts = stats.get("total_points", 0)
        mult = pick.get("multiplier", 1)
        predicted = predicted_by_element.get(el_id)
        results.append({
            "element": el_id,
            "name": p_data.get("web_name", str(el_id)),
            "position": pick.get("position", 0),
            "is_captain": pick.get("is_captain", False),
            "is_vice_captain": pick.get("is_vice_captain", False),
            "multiplier": mult,
            "actual_pts": actual_pts,
            "total_pts": actual_pts * mult,
            "predicted_xpts": round(predicted, 2) if predicted is not None else None,
            "diff": round(actual_pts - predicted, 2) if predicted is not None else None,
            "minutes": stats.get("minutes", 0),
            "goals": stats.get("goals_scored", 0),
            "assists": stats.get("assists", 0),
            "clean_sheet": bool(stats.get("clean_sheets", 0)),
            "bonus": stats.get("bonus", 0),
        })

    entry_history = picks_data.get("entry_history", {})
    xi_results = [r for r in results if r["position"] <= 11]
    return jsonify({
        "gw": gw,
        "total_actual": entry_history.get("points", 0),
        "total_predicted": round(sum(r["predicted_xpts"] or 0 for r in xi_results), 1),
        "active_chip": picks_data.get("active_chip"),
        "players": results,
    })


@app.route("/api/plan")
def get_plan():
    if not _state["token"]:
        return jsonify({"error": "Not logged in"}), 401

    bs = _state["bootstrap"] or {}
    events = bs.get("events", [])
    next_gw = next((e["id"] for e in events if e.get("is_next")), None)
    current_gw_event = next((e["id"] for e in events if e.get("is_current")), None)
    current_gw = next_gw or current_gw_event or 1

    my_team = None
    entry_info = None
    if _state["entry_id"]:
        try:
            my_team = fpl_api.my_team(_state["session"], _state["token"], _state["entry_id"])
        except Exception:
            pass
        try:
            entry_info = fpl_api.entry_info(_state["session"], _state["token"], _state["entry_id"])
        except Exception:
            pass

    try:
        from bot.updater import SeasonUpdater
        result = SeasonUpdater(horizon=6, max_candidates=150).run(
            current_gw, my_team=my_team, entry_info=entry_info
        )
        return jsonify({
            "gw": current_gw,
            "captain": result.get("captain"),
            "transfers_in": result.get("transfers_in"),
            "transfers_out": result.get("transfers_out"),
            "hits": result.get("hits"),
            "chip": result.get("chip"),
            "chip_reason": result.get("chip_reason"),
            "chip_plan": result.get("chip_plan"),
            "gw_plan": result.get("gw_plan"),
            "run_at": result.get("run_at"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    _load_session()
    print("FPL Auto running at http://localhost:5000")
    app.run(debug=False, port=5000, threaded=True)
