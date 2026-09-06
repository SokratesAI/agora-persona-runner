"""The way back from SokratesAI/marcus-backup into a running Marcus.

The assertions that carry the design, rather than the plumbing:

- the *live* revision is what gets sent, never the archived one, because the
  archived one is stale in exactly the case a restore is for;
- a dry run sends nothing at all, and `--apply` pins a restore point before it
  sends anything;
- every unreadable side exits 1 without writing, so "Marcus did not answer"
  can never be acted on as "Marcus is empty".
"""

import json
import types

import pytest

from tools import marcus_restore as mr


def _doc(rev=3, **stores):
    return {"rev": rev, "updatedAt": "2026-09-05T14:08:26.053Z",
            "data": stores or {"sessions": [1, 2], "weights": [1]}}


class _Response:
    def __init__(self, body, status=200):
        self._body = body if isinstance(body, bytes) else body.encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body, status=200):
    def open_(request, timeout=None):
        open_.seen.append(request)
        return _Response(body, status)
    open_.seen = []
    return open_


# --- parsing -----------------------------------------------------------------

def test_a_body_that_is_not_json_is_unreadable():
    with pytest.raises(mr.Unreadable, match="not JSON"):
        mr._as_state(b"<html>502 Bad Gateway</html>", "proxy")


def test_a_document_whose_data_is_null_is_refused():
    """The store reports `data: null` before anything has ever been written.

    Restoring that over a live Marcus is a wipe that exits 0, which is the one
    failure this whole tool would otherwise be capable of.
    """
    with pytest.raises(mr.Unreadable, match="not a Marcus state document"):
        mr._as_state(json.dumps({"rev": 0, "data": None}), "empty")


def test_a_real_document_parses():
    assert mr._as_state(json.dumps(_doc()), "x")["rev"] == 3


# --- reading the live side ---------------------------------------------------

def test_read_live_returns_the_document():
    live = mr.read_live("http://m/api/state", opener=_opener(json.dumps(_doc())))
    assert live["rev"] == 3


def test_read_live_refuses_a_non_200():
    with pytest.raises(mr.Unreadable, match="answered 503"):
        mr.read_live("http://m/api/state",
                     opener=_opener(json.dumps(_doc()), status=503))


def test_read_live_refuses_a_document_with_no_revision():
    body = json.dumps({"updatedAt": "", "data": {"sessions": []}})
    with pytest.raises(mr.Unreadable, match="no usable revision"):
        mr.read_live("http://m/api/state", opener=_opener(body))


def test_read_live_refuses_a_boolean_revision():
    """`True` is an `int` in Python and would be sent as a revision of 1."""
    body = json.dumps({"rev": True, "updatedAt": "", "data": {"sessions": []}})
    with pytest.raises(mr.Unreadable, match="no usable revision"):
        mr.read_live("http://m/api/state", opener=_opener(body))


def test_an_unreachable_marcus_is_unreadable_not_empty():
    def refuse(request, timeout=None):
        raise OSError("Connection refused")
    with pytest.raises(mr.Unreadable, match="could not be reached"):
        mr.read_live("http://m/api/state", opener=refuse)


# --- reading the archive -----------------------------------------------------

def test_read_backup_from_a_local_file(tmp_path):
    target = tmp_path / "state.json"
    target.write_text(json.dumps(_doc(rev=9)))
    assert mr.read_backup(path=str(target))["rev"] == 9


def test_read_backup_from_the_repo_passes_the_ref():
    import base64
    payload = base64.b64encode(json.dumps(_doc()).encode()).decode()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")

    assert mr.read_backup(repo="o/r", ref="abc123", run=run)["rev"] == 3
    assert calls[0][:2] == ["gh", "api"]
    assert calls[0][2] == "repos/o/r/contents/marcus-state.json?ref=abc123"


def test_read_backup_omits_the_ref_when_none_was_asked_for():
    import base64
    payload = base64.b64encode(json.dumps(_doc()).encode()).decode()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")

    mr.read_backup(repo="o/r", run=run)
    assert calls[0][2] == "repos/o/r/contents/marcus-state.json"


def test_a_failing_gh_call_is_unreadable():
    def run(argv, **kwargs):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="404 Not Found")
    with pytest.raises(mr.Unreadable, match="404 Not Found"):
        mr.read_backup(repo="o/r", run=run)


# --- the comparison ----------------------------------------------------------

def test_compare_covers_stores_the_backup_does_not_carry():
    """A store only the live side holds is one the restore drops silently."""
    live = _doc(sessions=[1, 2, 3], meals=[1])
    backup = _doc(sessions=[1], chat=[1, 2])
    rows = dict((key, (a, b)) for key, a, b in mr.compare(live, backup))
    assert rows["meals"] == (1, None)
    assert rows["chat"] == (None, 2)
    assert rows["sessions"] == (3, 1)


def test_report_marks_only_the_stores_that_change():
    lines = []
    live = _doc(sessions=[1], weights=[1, 2])
    backup = _doc(sessions=[1], weights=[1])
    mr.report(live, backup, mr.compare(live, backup), False, out=lines.append)
    text = "\n".join(lines)
    assert "weights" in text and "<- changes" in text
    assert [ln for ln in lines if "sessions" in ln and "<- changes" in ln] == []


# --- writing -----------------------------------------------------------------

def test_put_state_sends_the_revision_it_was_given():
    opener = _opener(json.dumps(_doc(rev=4)))
    mr.put_state(3, {"sessions": []}, "http://m/api/state", opener=opener)
    sent = json.loads(opener.seen[0].data)
    assert sent == {"rev": 3, "data": {"sessions": []}}
    assert opener.seen[0].get_method() == "PUT"


def test_write_restore_point_saves_the_live_document(tmp_path):
    live = _doc(rev=7)
    target = mr.write_restore_point(live, str(tmp_path))
    assert json.loads(open(target).read()) == live


# --- the command -------------------------------------------------------------

def _wire(monkeypatch, live, backup, put=None):
    calls = {"put": []}
    monkeypatch.setattr(mr, "read_live", lambda url=None, **kw: live)
    monkeypatch.setattr(mr, "read_backup", lambda **kw: backup)

    def put_state(rev, data, url=mr.DEFAULT_URL, **kw):
        calls["put"].append((rev, data))
        if put is not None:
            return put(rev, data)
        return {"rev": rev + 1, "updatedAt": "now", "data": data}

    monkeypatch.setattr(mr, "put_state", put_state)
    return calls


def test_a_dry_run_writes_nothing(monkeypatch, capsys):
    calls = _wire(monkeypatch, _doc(rev=5, sessions=[1]), _doc(rev=2, sessions=[1, 2]))
    assert mr.main([]) == 0
    assert calls["put"] == []
    assert "Nothing was written" in capsys.readouterr().out


def test_apply_sends_the_live_revision_not_the_archived_one(monkeypatch, tmp_path, capsys):
    """The archived rev is stale in exactly the case a restore is for."""
    live = _doc(rev=5, sessions=[1])
    backup = _doc(rev=2, sessions=[1, 2])
    calls = _wire(monkeypatch, live, backup)
    assert mr.main(["--apply", "--restore-point-dir", str(tmp_path)]) == 0
    assert calls["put"] == [(5, backup["data"])]
    assert "restored: rev 5 -> 6" in capsys.readouterr().out


def test_apply_pins_the_live_state_before_it_writes(monkeypatch, tmp_path):
    live = _doc(rev=5, sessions=[1])
    order = []
    monkeypatch.setattr(mr, "read_live", lambda url=None, **kw: live)
    monkeypatch.setattr(mr, "read_backup", lambda **kw: _doc(rev=2, sessions=[]))
    real_pin = mr.write_restore_point
    monkeypatch.setattr(mr, "write_restore_point",
                        lambda state, directory, **kw: (order.append("pin"),
                                                        real_pin(state, directory))[1])
    monkeypatch.setattr(mr, "put_state",
                        lambda rev, data, url=mr.DEFAULT_URL, **kw: (
                            order.append("put"), {"rev": rev + 1, "updatedAt": "",
                                                  "data": data})[1])
    assert mr.main(["--apply", "--restore-point-dir", str(tmp_path)]) == 0
    assert order == ["pin", "put"]
    pinned = list(tmp_path.glob("marcus-restore-point-*.json"))
    assert len(pinned) == 1
    assert json.loads(pinned[0].read_text()) == live


def test_a_refused_write_exits_1_and_says_nothing_changed(monkeypatch, tmp_path, capsys):
    def refuse(rev, data):
        raise mr.Unreadable("refused the write with 409: built on revision 5")
    _wire(monkeypatch, _doc(rev=5), _doc(rev=2), put=refuse)
    assert mr.main(["--apply", "--restore-point-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "409" in out and "Nothing was changed" in out


def test_an_unreadable_side_exits_1_without_writing(monkeypatch, capsys):
    calls = _wire(monkeypatch, _doc(), _doc())

    def boom(url=None, **kw):
        raise mr.Unreadable("http://m/api/state could not be reached: refused")

    monkeypatch.setattr(mr, "read_live", boom)
    assert mr.main(["--apply"]) == 1
    assert calls["put"] == []
    out = capsys.readouterr().out
    assert "CANNOT SEE" in out and "not a report that Marcus is empty" in out


def test_an_unwritable_restore_point_stops_before_the_put(monkeypatch, capsys):
    calls = _wire(monkeypatch, _doc(rev=5), _doc(rev=2))
    assert mr.main(["--apply", "--restore-point-dir", "/nope/nowhere"]) == 1
    assert calls["put"] == []
    assert "Nothing was sent to Marcus" in capsys.readouterr().out


def test_the_restore_point_default_outlives_the_cycle(monkeypatch, capsys):
    """`$NOVA_WORKSPACE` is a per-turn worktree on a concurrent cycle.

    A restore point written there is deleted with the turn that made it, which
    is the one moment it exists to survive.
    """
    monkeypatch.setenv("NOVA_WORKSPACE", "/data/workspace-concurrent/7-1")
    assert mr.RESTORE_POINT_DIR == "/data/workspace"
    calls = _wire(monkeypatch, _doc(rev=5), _doc(rev=2))
    monkeypatch.setattr(mr, "write_restore_point",
                        lambda state, directory, **kw: directory)
    assert mr.main(["--apply"]) == 0
    assert "restore point: /data/workspace" in capsys.readouterr().out
    assert calls["put"] == [(5, _doc(rev=2)["data"])]
