"""Look at a Nova page in a real browser, instead of trusting that it works.

Edvard, comments board 2026-08-14: *"Do you use Chromium to actually see
the ui? Or do you just trust blindly that the frontends you write just
works?"* The honest answer that morning was the second one.
`agora_runner.site_check` asks the server for HTML and JSON and checks the
fields the client reads -- real, and still not looking at the page.

This runs the page in headless Chromium and writes a PNG a cycle can then
open with the `Read` tool, which displays images. So a cycle can see what
it shipped.

    python3 -m tools.see_page              # every route in PAGE_ROUTES
    python3 -m tools.see_page /retro

**Run this from `Bash` (the bridge pod), which is the opposite of
`site_check`.** The bridge pod is the one with `node`, and it reaches
`nova-site:8083` fine -- the Cycle 186 handoff said it had no route into
the namespace and that was wrong, measured Cycle 187.

## Why it asserts on rendered text and not on status codes

The first working render came back a flawless empty wireframe: header,
capture box, eight cards, correct layout, zero characters of text.
Chromium had every shared library it needed and no fonts at all, so it
laid the page out and drew no glyphs. Nothing errored, nothing warned,
every status code was 200.

A check built out of status codes calls that healthy. So the assertion
here is on `textLen` and on console errors, and `/retro` rendering 2656
characters is the evidence that the page is really there.

The browser itself lives under `/data/workspace/nova-browser` rather than
in this repo -- it is 1.5GB of Chromium and unpacked Debian libraries.
`tools/browser/bootstrap.sh` rebuilds it from nothing in about three
minutes if that directory is ever lost.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from agora_runner.nova_site import PAGE_ROUTES

DEFAULT_ROOT = Path("/data/workspace/nova-browser")
DEFAULT_BASE = "http://nova-site:8083"

# Below this many characters of rendered text, assume the page did not
# really render. Calibrated against the failure rather than against the
# pages: the empty wireframe produced **0**, so this only has to separate
# nothing from something. `/costs` is the thinnest real page at 623
# characters because it is almost entirely SVG, and a threshold near that
# number would fail it the first time a label got shorter.
MIN_TEXT = 200

BOOTSTRAP_HINT = (
    "no browser at {root} -- run tools/browser/bootstrap.sh to build one "
    "(~3 minutes, ~1.5GB, no root needed)"
)


class BrowserMissing(RuntimeError):
    """The Chromium sysroot has not been bootstrapped in this pod."""


def browser_root() -> Path:
    return Path(os.environ.get("NOVA_BROWSER_ROOT", str(DEFAULT_ROOT)))


def render_env(root: Path) -> dict:
    """Environment for one `shot.js` run, or raise if it cannot work.

    Every entry here is load-bearing and each was a separate failure:
    `LD_LIBRARY_PATH` points at Debian libraries unpacked without root,
    `FONTCONFIG_*` at a fonts.conf rewritten to find them, and the skip
    flag exists because Playwright validates against the system package
    database, which will never know about a hand-built sysroot. `ldd` on
    the binary is the measurement that actually matters.
    """
    libdirs = root / "libdirs.txt"
    fonts = root / "fontconf" / "fonts.conf"
    for needed in (libdirs, fonts, root / "shot.js"):
        if not needed.exists():
            raise BrowserMissing(BOOTSTRAP_HINT.format(root=root))
    env = dict(os.environ)
    env.update(
        {
            "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS": "1",
            "PLAYWRIGHT_BROWSERS_PATH": str(root / "browsers"),
            "LD_LIBRARY_PATH": libdirs.read_text().strip(),
            "FONTCONFIG_PATH": str(fonts.parent),
            "FONTCONFIG_FILE": str(fonts),
        }
    )
    return env


def problems(rows) -> list:
    """What is wrong with these renders, in the order a reader wants it."""
    found = []
    for row in rows:
        where = row["path"]
        if row["status"] != 200:
            found.append(f"{where}: HTTP {row['status']}")
            continue
        if row["textLen"] < MIN_TEXT:
            found.append(
                f"{where}: rendered {row['textLen']} characters of text "
                f"(under {MIN_TEXT}) -- the page laid out but drew nothing, "
                "which usually means no fonts"
            )
        for err in row["consoleErrors"]:
            found.append(f"{where}: console error: {err}")
    return found


def render(paths, root=None, base=DEFAULT_BASE) -> list:
    root = root or browser_root()
    env = render_env(root)
    env["NOVA_SITE"] = base
    proc = subprocess.run(
        ["node", "shot.js", *paths],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    if not rows:
        raise RuntimeError(f"shot.js rendered nothing: {proc.stderr.strip()[:400]}")
    return rows


def main(argv=None) -> int:
    paths = list(argv if argv is not None else sys.argv[1:]) or list(PAGE_ROUTES)
    try:
        rows = render(paths)
    except (BrowserMissing, RuntimeError) as exc:
        print(exc)
        return 1
    root = browser_root()
    for row in rows:
        print(
            f"{row['path']:<12} {row['status']} {row['textLen']:>6} chars  "
            f"{root / 'shots'}/{_shot_name(row['path'])}.png"
        )
    found = problems(rows)
    for line in found:
        print(line)
    return 1 if found else 0


def _shot_name(path: str) -> str:
    """Mirror of the name `shot.js` gives a screenshot, so the printed path is real."""
    if path == "/":
        return "root"
    import re

    return re.sub(r"\W+", "_", path).strip("_")


if __name__ == "__main__":
    raise SystemExit(main())
