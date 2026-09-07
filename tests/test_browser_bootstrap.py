"""`tools/browser/bootstrap.sh` -- the script that rebuilds the browser environment.

The environment it builds is 1.2GB of downloaded Chromium, an apt sysroot
unpacked without root and a rewritten fonts.conf, living on a volume, in no
image and in no repo. This script is the only thing that can put it back.

Cycle 1130 measured that it could not. Its last step -- the one whose own
comment says "verify, don't assume" -- ran `./run.sh`, a file that has never
existed in this repo or in the built directory: exit 127. Under `set -e` that
took the whole rebuild down with it after three minutes of downloading, so the
script reported failure on work that had actually succeeded, and the one step
that would have caught a missing font never ran.

Nothing about that is visible from reading the script, which is why the check
here is mechanical: every path it executes has to resolve to a real file.
"""

import re
from pathlib import Path

import pytest

from tools import see_page

REPO = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO / "tools" / "browser" / "bootstrap.sh"


def unresolvable_scripts(text: str) -> list:
    """Paths the script executes that neither it nor the repo provides.

    Deliberately generous about what counts as an invocation and strict about
    what counts as resolved: a `./x` at the start of a command, and a
    `python3 -m tools.x`. Both were available to the cycle that wrote the
    broken line and neither was checked.
    """
    missing = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        run = re.match(r"^\./([\w./-]+)", stripped)
        if run and not (REPO / "tools" / "browser" / run.group(1)).exists():
            missing.append(stripped)
        for module in re.findall(r"python3 -m ([\w.]+)", stripped):
            if not (REPO / (module.replace(".", "/") + ".py")).exists():
                missing.append(stripped)
    return missing


def test_every_script_it_runs_exists():
    """The exact defect: a rebuild that dies on its own last line."""
    assert unresolvable_scripts(BOOTSTRAP.read_text()) == []


def test_the_detector_catches_the_line_that_shipped():
    """A green check on today's file proves nothing unless it can go red.

    This is the verification step verbatim as it stood from Cycle 170 until
    Cycle 1130 -- three weeks and every browser cycle in between.
    """
    broken = "# 5. verify, don't assume\n./run.sh / > /tmp/nova-browser-verify.json\n"
    assert unresolvable_scripts(broken) == ["./run.sh / > /tmp/nova-browser-verify.json"]
    assert unresolvable_scripts("python3 -m tools.no_such_module /") != []


def test_builder_and_consumer_agree_on_where_the_environment_lives():
    """A builder writing somewhere `see_page` does not look is a silent rebuild of nothing."""
    text = BOOTSTRAP.read_text()
    default = re.search(r"^R=\$\{NOVA_BROWSER_ROOT:-(\S+)\}$", text, re.M)
    assert default, "bootstrap.sh must take its root from NOVA_BROWSER_ROOT with a default"
    assert default.group(1) == str(see_page.DEFAULT_ROOT)


def test_it_ends_by_rendering_a_page():
    """Steps 1-4 can all succeed and still leave a browser that draws no text.

    Cycle 187: every shared library present, no fonts, every route HTTP 200 and
    innerText empty. So the script is not finished when the bytes are on disk --
    it is finished when a page has actually rendered through them.
    """
    commands = [
        line.strip()
        for line in BOOTSTRAP.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "tools.see_page" in commands[-1]
