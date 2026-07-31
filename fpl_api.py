"""FPL API wrapper. All write endpoints require an access_token from fpl_auth.login()."""
import requests

_BASE = "https://fantasy.premierleague.com/api"


def _headers(access_token: str, extra: dict = None) -> dict:
    h = {
        "X-API-Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def bootstrap(session: requests.Session) -> dict:
    """All players, teams, game settings. Public — no auth needed."""
    r = session.get(f"{_BASE}/bootstrap-static/")
    r.raise_for_status()
    return r.json()


def me(session: requests.Session, token: str) -> dict:
    """Current user info, including player.entry (the FPL entry/team ID)."""
    r = session.get(f"{_BASE}/me/", headers=_headers(token))
    r.raise_for_status()
    return r.json()


def my_team(session: requests.Session, token: str, entry_id: int) -> dict:
    """Current 15-player squad with picks, chips, and budget."""
    r = session.get(f"{_BASE}/my-team/{entry_id}/", headers=_headers(token))
    r.raise_for_status()
    return r.json()


def transfer(
    session: requests.Session,
    token: str,
    entry_id: int,
    event: int,
    transfers: list[dict],
    chip: str = None,
) -> dict:
    """
    Make one or more transfers.
    Each transfer dict: {element_in, element_out, purchase_price, selling_price}
    Prices are in tenths of £ (e.g. £7.5m = 75).
    For initial squad selection (no players yet), omit element_out and selling_price.
    """
    r = session.post(
        f"{_BASE}/transfers/",
        json={"transfers": transfers, "chip": chip, "entry": entry_id, "event": event},
        headers=_headers(token, {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://fantasy.premierleague.com/transfers",
            "Origin": "https://fantasy.premierleague.com",
        }),
    )
    r.raise_for_status()
    return r.json()


def update_picks(
    session: requests.Session,
    token: str,
    entry_id: int,
    picks: list[dict],
    chips: list = None,
) -> dict:
    """
    Update starting XI, captain, bench order — does NOT change squad composition.
    Each pick: {element, position, is_captain, is_vice_captain}
    Positions 1–11 = starting XI, 12–15 = bench (12 = first sub, GKP goes to 12 if benched).
    """
    r = session.post(
        f"{_BASE}/my-team/{entry_id}/",
        json={"picks": picks, "chips": chips or []},
        headers=_headers(token),
    )
    r.raise_for_status()
    return r.json()


def entry_info(session: requests.Session, token: str, entry_id: int) -> dict:
    """Entry details: team name, overall rank, bank balance, etc."""
    r = session.get(f"{_BASE}/entry/{entry_id}/", headers=_headers(token))
    r.raise_for_status()
    return r.json()
