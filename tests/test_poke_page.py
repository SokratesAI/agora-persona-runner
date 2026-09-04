"""`tools.poke_page` -- the interaction check, as opposed to the look-at-it check.

These pin the Python half only: choosing flags, running `poke.js`, and
turning its JSON lines into a verdict. The probes themselves need a real
browser and a real site and are exercised by running the tool, not by
these.

Two of them are about a failure this loop keeps repeating rather than
about this tool: a check that reports green when it measured nothing. A
probe that never reported must read as failed, and a security override
must not creep into the flags, because either one makes the tool agree
with whatever it was pointed at.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from tools import poke_page


def _root(tmp_path):
    (tmp_path / "libdirs.txt").write_text("/sysroot/lib\n")
    (tmp_path / "fontconf").mkdir()
    (tmp_path / "fontconf" / "fonts.conf").write_text("<fontconfig/>")
    (tmp_path / "browsers").mkdir()
    return tmp_path


def _row(probe, ok=True, detail=None, errors=None):
    return {"probe": probe, "ok": ok, "detail": detail or {}, "errors": errors or []}


def test_flags_carry_no_security_override():
    """The offline probes get a secure context from a localhost forwarder.

    An `--unsafely-treat-insecure-origin-as-secure` or a
    `--disable-web-security` here would let a probe pass because the
    browser stopped enforcing something a real phone enforces.
    """
    args = poke_page.chrome_args()
    assert args == ["--no-sandbox", "--disable-dev-shm-usage"]
    assert not any("unsafely" in a or "disable-web-security" in a for a in args)


def test_a_probe_that_did_not_report_is_a_failure():
    """Not a skip, and not silence. This is the whole point of `wanted`."""
    found = poke_page.problems([_row("search-focus")], ["search-focus", "offline-banner"])
    assert len(found) == 1
    assert "offline-banner" in found[0]
    assert "did not report" in found[0]


def test_a_failed_probe_reports_its_detail():
    found = poke_page.problems(
        [_row("replay-header", ok=False, detail={"replayed": None, "status": 200})],
        ["replay-header"],
    )
    assert found and "replay-header" in found[0]
    # The detail is what says *which* end broke; a bare FAILED sends the
    # next cycle back to the browser to find out.
    assert "replayed" in found[0]


def test_console_errors_fail_an_otherwise_green_probe():
    found = poke_page.problems([_row("search-focus", errors=["TypeError: x"])], ["search-focus"])
    assert found and "console error" in found[0]


def test_all_green_is_no_problems():
    names = list(poke_page.PROBES)
    assert poke_page.problems([_row(n) for n in names], names) == []


def test_poke_passes_base_and_copies_the_script(tmp_path, monkeypatch):
    """`node` runs with cwd=the sysroot, so the repo copy must be pushed there.

    `see_page` learned this the hard way: an edit to the version-controlled
    script silently did not take effect.
    """
    root = _root(tmp_path)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs["env"]
        return SimpleNamespace(stdout=json.dumps(_row("search-focus")) + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rows = poke_page.poke(["search-focus"], root=root, base="http://127.0.0.1:8111")

    assert rows == [_row("search-focus")]
    assert seen["cmd"] == ["node", "poke.js", "search-focus"]
    assert seen["env"]["NOVA_SITE"] == "http://127.0.0.1:8111"
    assert (root / "poke.js").exists()


def test_no_output_raises_rather_than_reporting_green(tmp_path, monkeypatch):
    """`poke.js` dying is not an empty list of problems."""
    root = _root(tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: SimpleNamespace(stdout="", stderr="FATAL boom")
    )
    with pytest.raises(RuntimeError, match="boom"):
        poke_page.poke(["search-focus"], root=root)


def test_main_exits_nonzero_when_a_probe_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(poke_page, "poke", lambda *a, **k: [_row("search-focus", ok=False)])
    assert poke_page.main(["search-focus"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_two_probe_lists_agree():
    """`poke_page.PROBES` and `poke.js`'s own map name the same probes.

    A probe lives in two files and neither one imports the other. Adding
    it to `poke.js` alone means nothing ever asks for it and the whole
    thing is dead code that reads as a shipped check; adding it to
    `poke_page.PROBES` alone means the script reports `unknown: true`,
    which `problems()` does turn into a failure -- but only once someone
    runs the tool against a real browser, which is not what CI does.

    Both halves were edited by hand when `galaxy-canvas` went in, which
    is the only reason this test exists: it is the guard for the mistake
    that change could have made silently.
    """
    src = (poke_page.Path(poke_page.__file__).parent / "browser" / "poke.js").read_text()
    body = src.split("\nconst PROBES = {", 1)[1].split("\n};", 1)[0]
    in_js = sorted(m.strip().strip("'\"") for m in
                   [line.split(":", 1)[0] for line in body.splitlines() if ":" in line])
    assert in_js, "could not read poke.js's PROBES map -- the parse above went stale, not the lists"
    assert in_js == sorted(poke_page.PROBES)
