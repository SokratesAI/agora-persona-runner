"""Look at a Nova page in a real browser, instead of trusting that it works.

The owner, comments board 2026-08-14: *"Do you use Chromium to actually see
the ui? Or do you just trust blindly that the frontends you write just
works?"* The honest answer that morning was the second one.
`agora_runner.site_check` asks the server for HTML and JSON and checks the
fields the client reads -- real, and still not looking at the page.

This runs the page in headless Chromium and writes a PNG a cycle can then
open with the `Read` tool, which displays images. So a cycle can see what
it shipped.

    python3 -m tools.see_page              # every route, at phone width
    python3 -m tools.see_page /retro
    python3 -m tools.see_page --width 1280 /retro
    python3 -m tools.see_page --base http://127.0.0.1:8099 /issues

## Why there is a `--base`

Until this flag, this tool could only render `nova-site`, which serves
`main`. So a UI change could not be looked at until *after* it was merged,
which is the wrong order for the one tool whose job is catching what tests
cannot see -- three separate cycles shipped a dead control with a green
suite that week.

Two targets make the flag useful, and both were measured on 2026-08-14.
`nova-site-preview:8083` is a second Deployment of this same image in
`agents`, so it renders whatever image is deployed there rather than the
live one. And the site runs *inside the bridge pod*: this repo's package
reads `COUCHDB_*` while the bridge pod holds the same credentials under
`CDB_*`, so mapping the five across is all a local run needs --

    COUCHDB_URL="$CDB_BASE" COUCHDB_USER="$CDB_USER" \
    COUCHDB_PASSWORD="$CDB_PASS" COUCHDB_DB="$CDB_DB" \
    COUCHDB_NOVA_DB="$CDB_NOVA_DB" NOVA_PORT=8111 \
    python3 run_nova_site.py

Measured against that server: `/` 200, `/api/health` 200, `/api/journal`
200 at 1,489,522 bytes, `/api/board?name=issues` 200 at 523,422 with the
real rows. So a local render is trustworthy for data, not only layout.
That is the `CouchDB answered HTTP 401` Cycle 192 stopped on.

**The first version of this paragraph said `python3 -m
agora_runner.nova_site_main` and that command did nothing at all.** The
module had no `__main__` guard, so it exited 0 with no output -- and the
port happened to be answered by a leftover static file server from an
earlier cycle, which serves `index.html` at `/` and a stdlib HTML 404 at
every `/api/` path. That is a positive result on the one route that proves
nothing and a negative on every route that would have proved something.
The guard exists now, so both spellings work; the lesson that outlived it
is that `/` returning the right page is not evidence the server under test
is the one answering.

## Why the default width is a phone

the owner reads this app on a phone. This tool rendered at 1280x1400 from the
day it was built, and at 1280 the capture box's four controls sit on one
comfortable line. At 390 the priority dropdown is 136px wide, which left
room for exactly one of the three target buttons on the first line and
pushed the other two onto a second -- which is what he filed on 2026-08-14
as "the issue, idea, note and priority dropdown are now just scrambled".

So a render at a width he never uses is not a check, it is a different
page. The default is the phone; `--width` is there for the desktop case,
which is the exception. The width is in the PNG's filename so two runs at
two widths do not overwrite each other.

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
import shutil
import subprocess
import sys
from pathlib import Path

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_site import PAGE_ROUTES

DEFAULT_ROOT = Path("/data/workspace/nova-browser")
DEFAULT_BASE = "http://nova-site:8083"

# The narrow side of the phone this app is actually opened on. Not a
# guess at a device: it is the width the layout bug was reproduced at.
PHONE_WIDTH = 390

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
    for needed in (libdirs, fonts, root / "browsers"):
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


def render(paths, root=None, base=DEFAULT_BASE, width=PHONE_WIDTH) -> list:
    root = root or browser_root()
    env = render_env(root)
    env["NOVA_SITE"] = base
    env["NOVA_WIDTH"] = str(width)
    # `node shot.js` runs with cwd=root, so the script that actually runs
    # is the copy in the sysroot and not the one in this repo. Nothing kept
    # those in step -- `bootstrap.sh` does not install it, so the root copy
    # was placed by hand once and has been whatever a cycle last left there.
    # An edit to the version-controlled file would silently not take effect,
    # which is the worst possible failure for the one tool whose whole job
    # is showing a cycle what it really shipped. Copy it every run.
    shutil.copyfile(Path(__file__).resolve().parent / "browser" / "shot.js", root / "shot.js")
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
    args = list(argv if argv is not None else sys.argv[1:])
    width = PHONE_WIDTH
    base = DEFAULT_BASE
    if "--base" in args:
        at = args.index("--base")
        # Same failure as `--width` below: a target that was asked for and
        # silently ignored means a cycle looks at the live site while
        # believing it is looking at its own branch, which is worse than
        # not looking at all.
        base = args[at + 1]
        del args[at : at + 2]
    if "--width" in args:
        at = args.index("--width")
        # Fail loudly on a missing or unparseable value rather than
        # silently rendering at the default -- a width that was asked for
        # and quietly ignored is the failure this whole module is about.
        width = int(args[at + 1])
        del args[at : at + 2]
    paths = args or list(PAGE_ROUTES)
    try:
        rows = render(paths, base=base, width=width)
    except (BrowserMissing, RuntimeError) as exc:
        print(exc)
        return 1
    root = browser_root()
    for row in rows:
        print(
            f"{row['path']:<12} {row['status']} {row['textLen']:>6} chars  "
            f"{root / 'shots'}/{_shot_name(row['path'], width)}.png"
        )
    found = problems(rows)
    for line in found:
        print(line)
    return 1 if found else 0


def _shot_name(path: str, width: int = PHONE_WIDTH) -> str:
    """Mirror of the name `shot.js` gives a screenshot, so the printed path is real."""
    import re

    stem = "root" if path == "/" else re.sub(r"\W+", "_", path).strip("_")
    return f"{stem}-{width}"


if __name__ == "__main__":
    raise SystemExit(main())
