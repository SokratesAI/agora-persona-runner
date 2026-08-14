"""`tools.see_page` -- the browser check a cycle uses to look at what it shipped.

The failure these pin is the one that actually happened (Cycle 187): a
browser with every shared library and no fonts renders the page perfectly
and draws no text. Status codes were all 200 and nothing logged a warning,
so a check built on status alone reports a healthy site.
"""

import json
import subprocess

import pytest

from agora_runner.nova_site import PAGE_ROUTES
from tools import see_page


def _root(tmp_path, *, fonts=True, libdirs=True, shot=True):
    if libdirs:
        (tmp_path / "libdirs.txt").write_text("/sysroot/lib:/sysroot/usr/lib\n")
    if fonts:
        (tmp_path / "fontconf").mkdir()
        (tmp_path / "fontconf" / "fonts.conf").write_text("<fontconfig/>")
    if shot:
        (tmp_path / "shot.js").write_text("// stub")
    return tmp_path


def _row(path="/", status=200, text_len=3000, errors=()):
    return {
        "path": path,
        "status": status,
        "textLen": text_len,
        "head": "",
        "consoleErrors": list(errors),
    }


def test_empty_render_is_a_problem_even_though_the_status_is_200():
    """The fonts trap. 200, no console errors, and nothing on the page."""
    found = see_page.problems([_row(path="/retro", text_len=0)])
    assert len(found) == 1
    assert "/retro" in found[0]
    assert "0 characters" in found[0]
    assert f"under {see_page.MIN_TEXT}" in found[0]


def test_a_real_render_is_clean():
    assert see_page.problems([_row(text_len=3880), _row(path="/retro", text_len=2656)]) == []


def test_the_thinnest_real_page_is_not_a_false_positive():
    """`/costs` measured 623 characters live -- almost all of it is SVG."""
    assert see_page.problems([_row(path="/costs", text_len=623)]) == []


def test_console_errors_are_reported_per_page():
    found = see_page.problems(
        [_row(errors=["TypeError: x is not a function", "404 loading /bundle.js"])]
    )
    assert len(found) == 2
    assert all(line.startswith("/: console error:") for line in found)


def test_a_non_200_reports_the_status_and_nothing_else():
    """A 404 already explains itself; the text assertion below would be noise."""
    found = see_page.problems([_row(path="/retro", status=404, text_len=22)])
    assert found == ["/retro: HTTP 404"]


def test_render_env_carries_every_load_bearing_variable(tmp_path):
    env = see_page.render_env(_root(tmp_path))
    assert env["LD_LIBRARY_PATH"] == "/sysroot/lib:/sysroot/usr/lib"
    assert env["FONTCONFIG_FILE"] == str(tmp_path / "fontconf" / "fonts.conf")
    assert env["FONTCONFIG_PATH"] == str(tmp_path / "fontconf")
    assert env["PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS"] == "1"
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "browsers")


@pytest.mark.parametrize("missing", ["fonts", "libdirs", "shot"])
def test_a_half_built_sysroot_says_how_to_build_it(tmp_path, missing):
    """Missing fonts must fail loudly here rather than silently render nothing."""
    root = _root(tmp_path, **{missing: False})
    with pytest.raises(see_page.BrowserMissing) as exc:
        see_page.render_env(root)
    assert "bootstrap.sh" in str(exc.value)


def test_shot_name_matches_what_shot_js_writes():
    """The printed PNG path is a promise; a mismatch sends the reader to nothing."""
    assert see_page._shot_name("/") == "root"
    assert see_page._shot_name("/retro") == "retro"
    assert see_page._shot_name("/api/journal") == "api_journal"


def test_default_paths_are_the_routes_the_server_actually_serves(tmp_path, monkeypatch):
    """No second page list. A route added to the server is rendered by this."""
    seen = {}

    def fake_render(paths, root=None, base=see_page.DEFAULT_BASE):
        seen["paths"] = paths
        return [_row(path=p) for p in paths]

    monkeypatch.setattr(see_page, "render", fake_render)
    monkeypatch.setattr(see_page, "browser_root", lambda: tmp_path)
    assert see_page.main([]) == 0
    assert seen["paths"] == list(PAGE_ROUTES)


def test_main_exits_1_when_a_page_rendered_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(see_page, "render", lambda p, **k: [_row(path=p[0], text_len=0)])
    monkeypatch.setattr(see_page, "browser_root", lambda: tmp_path)
    assert see_page.main(["/retro"]) == 1


def test_main_reports_a_missing_browser_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(see_page, "browser_root", lambda: tmp_path / "nope")
    assert see_page.main(["/"]) == 1


def test_render_refuses_to_report_success_on_empty_output(tmp_path, monkeypatch):
    """`shot.js` dying mid-run must not read as a clean pass with no pages."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr="FATAL boom"),
    )
    with pytest.raises(RuntimeError, match="boom"):
        see_page.render(["/"], root=_root(tmp_path))


def test_render_parses_one_json_object_per_page(tmp_path, monkeypatch):
    out = "\n".join(json.dumps(_row(path=p)) for p in ("/", "/retro"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out + "\n", stderr=""),
    )
    rows = see_page.render(["/", "/retro"], root=_root(tmp_path))
    assert [r["path"] for r in rows] == ["/", "/retro"]
