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


def _save_session():
    with open(SESSION_FILE, "w") as f:
        json.dump({"token": _state["token"], "entry_id": _state["entry_id"]}, f)


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


def _enrich_picks(picks: list, bootstrap: dict) -> list:
    """Add player name, team, position, and price to each pick dict."""
    players_by_id = {p["id"]: p for p in bootstrap["elements"]}
    teams_by_id = {t["id"]: t["name"] for t in bootstrap["teams"]}
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    for pick in picks:
        p = players_by_id.get(pick.get("element"), {})
        pick["name"] = f"{p.get('first_name', '')} {p.get('second_name', '')}".strip()
        pick["web_name"] = p.get("web_name", pick["name"])
        pick["team"] = teams_by_id.get(p.get("team"), "")
        pick["pos"] = pos_map.get(p.get("element_type"), "")  # GKP/DEF/MID/FWD — keeps original "position" (lineup slot 1-15) intact
        pick["price"] = p.get("now_cost", 0) / 10
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


@app.route("/api/login/browser", methods=["POST"])
def browser_login_start():
    """Kicks off a background thread that opens a Chromium browser for login."""
    if _browser_login["status"] == "pending":
        return jsonify({"status": "pending"})

    def run():
        _browser_login["status"] = "pending"
        _browser_login["error"] = None
        try:
            token, session = fpl_auth.login_browser()
            _state["token"] = token
            _state["session"] = session
            user = fpl_api.me(session, token)
            _state["entry_id"] = user["player"].get("entry")
            _state["bootstrap"] = fpl_api.bootstrap(session)
            _save_session()
            _browser_login["status"] = "done"
        except Exception as e:
            _browser_login["status"] = "error"
            _browser_login["error"] = str(e)

    threading.Thread(target=run, daemon=True).start()
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


if __name__ == "__main__":
    _load_session()
    print("FPL Auto running at http://localhost:5000")
    app.run(debug=False, port=5000, threaded=True)
