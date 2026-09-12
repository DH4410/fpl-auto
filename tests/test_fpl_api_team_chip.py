import fpl_api


class _Response:
    ok = True
    content = b"{}"
    status_code = 200
    reason = "OK"
    text = "{}"

    def json(self):
        return {}


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


def test_update_picks_uses_singular_chip_wire_field():
    session = _Session()
    picks = [{"element": 1, "position": 1, "is_captain": True, "is_vice_captain": False}]

    fpl_api.update_picks(session, "token", 123, picks, chips=["bboost"])

    _, kwargs = session.calls[-1]
    assert kwargs["json"]["chip"] == "bboost"
    assert "chips" not in kwargs["json"]
    assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_update_picks_sends_null_chip_when_no_chip_selected():
    session = _Session()

    fpl_api.update_picks(session, "token", 123, [], chips=[])

    _, kwargs = session.calls[-1]
    assert kwargs["json"] == {"picks": [], "chip": None}


def test_update_picks_rejects_multiple_team_chips():
    session = _Session()

    try:
        fpl_api.update_picks(session, "token", 123, [], chips=["bboost", "3xc"])
    except ValueError as exc:
        assert "at most one team chip" in str(exc)
    else:
        raise AssertionError("expected ValueError for multiple team chips")
