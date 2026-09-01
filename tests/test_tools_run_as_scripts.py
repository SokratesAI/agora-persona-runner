"""Every tool in `tools/` imports as a plain script, not only as `-m`.

`python3 tools/mirror_journal.py` used to die with
`ModuleNotFoundError: No module named 'agora_runner'`, because running a
file directly puts *that file's directory* on `sys.path` — `tools/` — and
the repo root, where `agora_runner` lives, is nowhere on it. `python3 -m
tools.mirror_journal` works because `-m` puts the current directory
first instead.

I filed this against myself three times in six days and never fixed it:

    2026-08-16 (Cycle 240) — ... `prompt.md` names the file path form, so
    a cycle following its own instructions hits this.
    2026-08-20 (Cycle 290) — ... Cost me three calls this cycle.
    2026-08-21 (Cycle 291) — ... One-line fix in `prompt.md`, not yet made.

The one-line fix in `prompt.md` was the wrong fix, which is probably why
three cycles filed it and none did it. `prompt.md` cited the broken form
once; the reason cycles kept typing it is that it is the obvious way to
run a file. Seventeen tools had the fault, so correcting one citation
would have left sixteen traps and a rule to remember. The three-line
bootstrap above each `agora_runner` import makes the obvious form work
instead.

The eighteenth, `gc_orphan_chunks.py`, already carried a hand-written
version of exactly this bootstrap. So one cycle of mine found the fix,
applied it where it was standing, and never looked at the other
seventeen — which is the same shape as filing the bug three times. Its
line is replaced by the shared block here so all eighteen read alike.

This test is the part that lasts, and its first version was dangerous in
a way worth recording. It probed each tool with `--help` in a subprocess,
which is fine for argparse and is not what four of these are: `--help`
made `split_journal.py` execute for real and reach for CouchDB, which
answered 401. A test that runs my own vault-writing tools to check an
import is a worse bug than the one it is checking.

So the probe below never calls `main`. `runpy.run_path` reproduces script
semantics exactly — it is what puts the file's own directory at
`sys.path[0]`, which is the whole fault — but with `run_name` set to
something other than `"__main__"` the guard at the bottom of each tool
does not fire, so only the imports run. Measured against the unpatched
tree: 17 of the 18 probes fail, and the one that passes is
`gc_orphan_chunks.py`, for the reason above. So this is not vacuous.
"""

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def _imports_agora_runner(path):
    return any(line.startswith(("from agora_runner", "import agora_runner"))
               for line in path.read_text(encoding="utf-8").split("\n"))


AFFECTED = sorted(p.name for p in TOOLS.glob("*.py") if _imports_agora_runner(p))

# `rolling.py` is a compatibility shim for a module that moved into
# `agora_runner/`: it is imports and nothing else, so it has no `main` to
# guard and the probe cannot set anything running. Every other affected
# tool is a command and must have the guard.
NO_MAIN = {"rolling.py"}

# Executed in a subprocess so a tool that turns out to have side effects at
# import time cannot reach into the test session, and so `sys.path` edits by
# one tool cannot mask a second tool's fault.
PROBE = """
import runpy, sys
runpy.run_path(sys.argv[1], run_name="probe_not_main")
"""


def test_there_are_tools_to_check():
    """A glob that matched nothing would make every case below vacuous."""
    assert len(AFFECTED) > 15


@pytest.mark.parametrize("name", sorted(set(AFFECTED) - NO_MAIN))
def test_has_a_main_guard(name):
    """The probe below relies on this, so state it rather than assume it."""
    text = (TOOLS / name).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in text


@pytest.mark.parametrize("name", sorted(NO_MAIN))
def test_the_exempt_ones_really_have_no_main(name):
    """Otherwise the exemption above quietly turns into a hole."""
    text = (TOOLS / name).read_text(encoding="utf-8")
    assert "def main(" not in text


@pytest.mark.parametrize("name", AFFECTED)
def test_imports_as_a_plain_script(name):
    proc = subprocess.run(
        [sys.executable, "-c", PROBE, str(TOOLS / name)],
        capture_output=True, text=True, timeout=60,
        # cwd is deliberately *not* the repo root: that is what `-m` gives
        # you for free, and relying on it is the bug this test is about.
        cwd=str(Path(sys.executable).parent),
    )
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_bootstrap_is_present_wherever_agora_runner_is_imported():
    """The importing files are the ones that need it, and no others.

    Without this, a tool added next month gets the fault back and the
    parametrised case above only catches it if whoever adds it also knows
    what the probe is for. This states the rule directly, and it is also
what stops a second hand-written variant appearing beside the block.
    """
    missing = [name for name in AFFECTED
               if "parents[1]" not in (TOOLS / name).read_text(encoding="utf-8")]
    assert not missing, "no sys.path bootstrap in: %s" % ", ".join(missing)
