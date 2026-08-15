"""`tools.see_page` -- the browser check a cycle uses to look at what it shipped.

The failure these pin is the one that actually happened (Cycle 187): a
browser with every shared library and no fonts renders the page perfectly
and draws no text. Status codes were all 200 and nothing logged a warning,
so a check built on status alone reports a healthy site.
"""

import json
import pathlib
import subprocess
from types import SimpleNamespace

import pytest

from agora_runner.nova_site import PAGE_ROUTES
from tools import see_page


def _root(tmp_path, *, fonts=True, libdirs=True, browsers=True):
    if libdirs:
        (tmp_path / "libdirs.txt").write_text("/sysroot/lib:/sysroot/usr/lib\n")
    if fonts:
        (tmp_path / "fontconf").mkdir()
        (tmp_path / "fontconf" / "fonts.conf").write_text("<fontconfig/>")
    if browsers:
        (tmp_path / "browsers").mkdir()
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


@pytest.mark.parametrize("missing", ["fonts", "libdirs", "browsers"])
def test_a_half_built_sysroot_says_how_to_build_it(tmp_path, missing):
    """Missing fonts must fail loudly here rather than silently render nothing."""
    root = _root(tmp_path, **{missing: False})
    with pytest.raises(see_page.BrowserMissing) as exc:
        see_page.render_env(root)
    assert "bootstrap.sh" in str(exc.value)


def test_shot_name_matches_what_shot_js_writes():
    """The printed PNG path is a promise; a mismatch sends the reader to nothing.

    The width is in the name because two renders at two widths are the
    point of having a width at all, and without it the second silently
    overwrites the first -- so a cycle comparing a phone against a desktop
    would be looking at the same picture twice.
    """
    assert see_page._shot_name("/", 390) == "root-390"
    assert see_page._shot_name("/retro", 390) == "retro-390"
    assert see_page._shot_name("/api/journal", 1280) == "api_journal-1280"


def test_shot_js_builds_the_same_name_this_module_prints():
    """The two sides of that promise are in two languages; read the other one."""
    js = (
        pathlib.Path(see_page.__file__).resolve().parent
        / "browser" / "shot.js"
    ).read_text(encoding="utf-8")
    assert "`shots/${name}-${width}.png`" in js, js


def test_the_default_width_is_the_phone_he_reads_this_on():
    """1280 was the old default and is why a phone layout bug shipped."""
    assert see_page.PHONE_WIDTH == 390


def test_the_width_reaches_the_browser(tmp_path, monkeypatch):
    """`--width` that is parsed and then not passed on is the silent failure."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["width"] = kwargs["env"].get("NOVA_WIDTH")
        return SimpleNamespace(stdout=json.dumps(_row()), stderr="")

    monkeypatch.setattr(see_page, "render_env", lambda root: {})
    monkeypatch.setattr(see_page.subprocess, "run", fake_run)
    see_page.render(["/"], root=tmp_path, width=1280)
    assert seen["width"] == "1280"


def test_main_passes_its_parsed_width_through_and_keeps_the_paths(tmp_path, monkeypatch):
    seen = {}

    def fake_render(paths, root=None, base=see_page.DEFAULT_BASE, width=None):
        seen.update(paths=paths, width=width)
        return [_row(path=p) for p in paths]

    monkeypatch.setattr(see_page, "render", fake_render)
    monkeypatch.setattr(see_page, "browser_root", lambda: tmp_path)
    assert see_page.main(["--width", "1280", "/retro"]) == 0
    assert seen == {"paths": ["/retro"], "width": 1280}
    assert see_page.main(["/retro"]) == 0
    assert seen["width"] == see_page.PHONE_WIDTH


def test_default_paths_are_the_routes_the_server_actually_serves(tmp_path, monkeypatch):
    """No second page list. A route added to the server is rendered by this."""
    seen = {}

    def fake_render(paths, root=None, base=see_page.DEFAULT_BASE, width=None):
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


def test_the_repo_copy_of_shot_js_is_the_one_that_runs(tmp_path, monkeypatch):
    """`node shot.js` runs with cwd=root, and nothing installs it there.

    `bootstrap.sh` builds the sysroot and does not mention `shot.js`, so the
    copy beside the browser was hand-placed and is free to drift from the
    repo. An edit to the file under review would then change nothing about
    what runs -- and this module exists to show a cycle what it shipped.
    """

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(stdout=json.dumps(_row()), stderr="")

    monkeypatch.setattr(see_page, "render_env", lambda root: {})
    monkeypatch.setattr(see_page.subprocess, "run", fake_run)
    stale = tmp_path / "shot.js"
    stale.write_text("// a copy some cycle left here in 2026")
    see_page.render(["/"], root=tmp_path)
    fresh = (
        pathlib.Path(see_page.__file__).resolve().parent / "browser" / "shot.js"
    ).read_text(encoding="utf-8")
    assert stale.read_text(encoding="utf-8") == fresh


def test_the_base_reaches_the_browser(tmp_path, monkeypatch):
    """A `--base` that is parsed and then not passed on renders the live site.

    That is the worst possible failure for this flag: the cycle believes it
    is looking at its own branch and is actually looking at `main`.
    """
    seen = {}

    def fake_render(paths, root=None, base=see_page.DEFAULT_BASE, width=None):
        seen.update(base=base, paths=paths, width=width)
        return [_row(path=p) for p in paths]

    monkeypatch.setattr(see_page, "render", fake_render)
    monkeypatch.setattr(see_page, "browser_root", lambda: tmp_path)
    assert see_page.main(["--base", "http://127.0.0.1:8099", "--width", "1280", "/retro"]) == 0
    assert seen == {"base": "http://127.0.0.1:8099", "paths": ["/retro"], "width": 1280}
    assert see_page.main(["/retro"]) == 0
    assert seen["base"] == see_page.DEFAULT_BASE


def test_the_base_is_what_shot_js_is_told_to_open(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["site"] = kwargs["env"].get("NOVA_SITE")
        return SimpleNamespace(stdout=json.dumps(_row()), stderr="")

    monkeypatch.setattr(see_page, "render_env", lambda root: {})
    monkeypatch.setattr(see_page.subprocess, "run", fake_run)
    see_page.render(["/"], root=tmp_path, base="http://nova-site-preview:8083")
    assert seen["site"] == "http://nova-site-preview:8083"


def test_the_sysroot_installs_an_emoji_font():
    """Tofu pills read as a broken stylesheet, so a screenshot without this lies.

    The board draws its status and priority as emoji. Cycle 196 rendered the
    issues page against a sysroot with only `fonts-dejavu-core` and got an
    empty box in place of every pill -- a screenshot that misrepresents the
    page is worse than none, because it gets acted on.
    """
    script = (pathlib.Path(see_page.__file__).resolve().parent / "browser" / "bootstrap.sh").read_text()
    assert "fonts-noto-color-emoji" in script
