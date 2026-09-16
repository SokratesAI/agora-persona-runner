"""`tools.research_index` -- the I/O half, driven through `main(argv)`."""

import pytest

from tools import research_index

FOLDER = research_index.FOLDER
LISTING = "\n".join(FOLDER + n for n in (
    "nas-linuxserver-survey-2026-08-30.md",
    "nas-k3s-2026-08-29.md",
    "cycle-ledger.json",
))


@pytest.fixture
def listing(tmp_path):
    p = tmp_path / "listing.txt"
    p.write_text(LISTING + "\n")
    return str(p)


def test_a_covered_topic_exits_2(listing, capsys):
    code = research_index.main(["--paths", listing, "--for",
                                "what else from linuxserver to put on the NAS"])
    assert code == 2
    assert "ALREADY RESEARCHED" in capsys.readouterr().out


def test_an_uncovered_topic_exits_0(listing, capsys):
    code = research_index.main(["--paths", listing, "--for", "grafana dashboards"])
    assert code == 0
    assert "Nothing written yet" in capsys.readouterr().out


def test_no_topic_prints_the_index(listing, capsys):
    assert research_index.main(["--paths", listing]) == 0
    out = capsys.readouterr().out
    assert "2 research documents on file" in out
    assert "nas-k3s-2026-08-29" in out
    assert "cycle-ledger" not in out          # only .md is research prose


def test_an_empty_listing_refuses_rather_than_reporting_nothing(tmp_path, capsys):
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    code = research_index.main(["--paths", str(empty), "--for", "anything"])
    assert code == 1
    err = capsys.readouterr().err
    assert "refusing to report" in err
    assert "Nothing written yet" not in capsys.readouterr().out


def test_an_unreadable_listing_exits_1(tmp_path, capsys):
    code = research_index.main(["--paths", str(tmp_path / "gone.txt"), "--for", "x"])
    assert code == 1
    assert "could not list" in capsys.readouterr().err


def test_the_bridge_fallback_runs_when_the_library_listing_is_empty(monkeypatch, tmp_path, capsys):
    """The pod a cycle runs this from 401s on the library route.

    Without the fallback the tool is correct and useless: it exits 1 in the
    only shell anybody calls it from.
    """
    monkeypatch.setattr(research_index, "BRIDGE_VAULT_TOOL", str(tmp_path / "vt.py"))
    (tmp_path / "vt.py").write_text("x")
    monkeypatch.setattr(research_index, "_md", research_index._md)

    class Done:
        returncode = 0
        stdout = LISTING

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Done()

    monkeypatch.setattr(research_index.subprocess, "run", fake_run)
    monkeypatch.setitem(__import__("sys").modules, "agora_runner.vault",
                        type("m", (), {"vault_list_prefix": staticmethod(lambda p: [])}))

    assert research_index.main(["--for", "linuxserver on the NAS"]) == 2
    assert calls and calls[0][2] == "ls"
    assert "ALREADY RESEARCHED" in capsys.readouterr().out


def test_a_failing_fallback_still_refuses(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(research_index, "BRIDGE_VAULT_TOOL", str(tmp_path / "vt.py"))
    (tmp_path / "vt.py").write_text("x")

    class Done:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(research_index.subprocess, "run", lambda cmd, **kw: Done())
    monkeypatch.setitem(__import__("sys").modules, "agora_runner.vault",
                        type("m", (), {"vault_list_prefix": staticmethod(lambda p: [])}))
    assert research_index.main(["--for", "anything"]) == 1
    assert "refusing to report" in capsys.readouterr().err
