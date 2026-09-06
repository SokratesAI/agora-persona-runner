"""tools.persona_restrictions -- which personas can run a shell right now.

The thing worth guarding here is the classification, not the HTTP. A
persona has the bridge pod's shell when it is `claude-cli:` AND not
restricted; both halves matter and the tool must not re-spell the rule
(`agora_runner.turns.runs_on_the_bridge` owns it), so these cases feed it
personas that differ in exactly one field.
"""
import io
import json

from tools import persona_restrictions as pr


def _persona(pid, name, model, restricted=None):
    row = {"id": pid, "name": name, "model": model}
    if restricted is not None:
        row["claudeCliRestricted"] = restricted
    return row


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener_for(personas):
    """Serves `GET /personas` and `GET /personas/{id}` off a list."""
    by_id = {p["id"]: p for p in personas}

    def opener(url, timeout=None):
        path = url.split("8080", 1)[-1] if "8080" in url else url
        if path.endswith("/personas"):
            return _Resp(json.dumps(
                {"personas": [{"id": p["id"]} for p in personas]}).encode())
        pid = path.rsplit("/", 1)[-1]
        if pid not in by_id:
            raise RuntimeError(f"404 {pid}")
        return _Resp(json.dumps({"persona": by_id[pid]}).encode())

    return opener


UNRESTRICTED = _persona("a", "Devil's advocate", "claude-cli:claude-haiku-4-5-20251001")
RESTRICTED = _persona("b", "Plain assistant", "claude-cli:claude-haiku-4-5-20251001",
                      restricted=True)
FLAG_FALSE = _persona("c", "Nova", "claude-cli:claude-opus-5", restricted=False)
OTHER_PROVIDER = _persona("d", "Agora", "gemini:gemini-3.6-flash")
OTHER_RESTRICTED = _persona("e", "Gemini", "gemini:gemini-3.5-flash-lite",
                            restricted=True)


def test_the_flag_is_what_moves_a_claude_cli_persona_out_of_the_shell_group():
    """Same provider, same model, one field different -- so a pass here
    cannot come from the provider check alone."""
    text, status = pr.judge([UNRESTRICTED, RESTRICTED])
    assert status == 0
    shell, rest = text.split("RESTRICTED --", 1)
    assert "Devil's advocate" in shell
    assert "Devil's advocate" not in rest
    assert "Plain assistant" in rest
    assert "Plain assistant" not in shell


def test_an_explicit_false_still_has_the_shell():
    """`claudeCliRestricted: False` is what the two Nova personas carry and
    it must read the same as the field being absent, not as restricted."""
    text, status = pr.judge([FLAG_FALSE])
    assert status == 0
    assert "HAS THE BRIDGE POD'S SHELL -- 1 persona(s)." in text
    assert "RESTRICTED --" not in text


def test_another_provider_is_never_counted_as_holding_a_shell():
    """A gemini persona is a stateless call from the runner process. It has
    no shell whether or not the flag is set, and calling it `RESTRICTED`
    would claim a control that is doing nothing."""
    text, status = pr.judge([OTHER_PROVIDER, OTHER_RESTRICTED])
    assert status == 0
    assert "NEVER ON THE BRIDGE -- 2 persona(s)" in text
    assert "HAS THE BRIDGE POD'S SHELL -- none." in text
    assert "RESTRICTED --" not in text


def test_everything_restricted_is_still_exit_0():
    """There is no exit 2: which personas ought to be restricted is the
    owner's call. A tool that alarmed here would be voting."""
    _, status = pr.judge([RESTRICTED])
    assert status == 0
    _, status = pr.judge([UNRESTRICTED])
    assert status == 0


def test_unreadable_is_exit_1_and_never_prints_a_count():
    """No instrument is not a measurement of an unrestricted platform --
    the failure mode this whole file exists to avoid."""
    text, status = pr.judge([], error="could not read http://agora/personas: boom")
    assert status == 1
    assert text.startswith("NO INSTRUMENT --")
    assert "HAS THE BRIDGE POD'S SHELL" not in text


def test_fetch_reads_each_persona_detail_because_the_roster_omits_the_flag():
    """The roster response carries no `claudeCliRestricted` (measured on the
    live app), so a fetch that trusted the list would report every persona
    as unrestricted."""
    personas, error = pr.fetch(opener=_opener_for([UNRESTRICTED, RESTRICTED]))
    assert error is None
    assert [p["name"] for p in personas] == ["Devil's advocate", "Plain assistant"]
    assert personas[1]["claudeCliRestricted"] is True


def test_fetch_reports_an_unreadable_detail_rather_than_dropping_the_row():
    def opener(url, timeout=None):
        if url.endswith("/personas"):
            return _Resp(json.dumps({"personas": [{"id": "a"}]}).encode())
        raise RuntimeError("boom")

    personas, error = pr.fetch(opener=opener)
    assert personas == []
    assert "boom" in error


def test_fetch_refuses_a_roster_row_with_no_id():
    def opener(url, timeout=None):
        return _Resp(json.dumps({"personas": [{"name": "nameless"}]}).encode())

    personas, error = pr.fetch(opener=opener)
    assert personas == []
    assert "no id" in error


def test_main_exits_1_when_agora_is_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(pr, "fetch", lambda: ([], "could not read: down"))
    assert pr.main([]) == 1
    assert "NO INSTRUMENT" in capsys.readouterr().out


def test_fetch_refuses_a_detail_response_with_no_persona_record():
    """A 200 that is not a persona record must not be skipped. Dropping it
    would shrink the count silently, which is the same 'no instrument reads
    as clean' failure the tool's exit 1 exists to prevent."""
    def opener(url, timeout=None):
        if url.endswith("/personas"):
            return _Resp(json.dumps({"personas": [{"id": "a"}]}).encode())
        return _Resp(json.dumps({"error": "gone"}).encode())

    personas, error = pr.fetch(opener=opener)
    assert personas == []
    assert "no persona record at /personas/a" in error
