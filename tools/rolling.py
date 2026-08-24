"""Compatibility shim: the rolling engine now lives in `agora_runner/`.

It moved on 2026-08-16 for one reason and it is a deployment fact, not a
taste: the container image copies `agora_runner/` and does not copy
`tools/`, so anything the *site* has to run cannot live here. Issue #93
asked for a button on the page that archives an answered Needs Edvard  (not-prose: quoting a literal)
item, which is exactly the transform `roll_needs_edvard.py` already did
from a shell -- and the alternative to moving this file was writing a
second implementation of it inside `agora_runner/`, i.e. the third-fix-of-
the-same-shape that this loop keeps promising itself it will stop doing.

Nothing about the engine changed. This module stays so that
`from tools import rolling` and `from tools.rolling import RollSpec` keep
meaning what they meant, for the four CLI tools and the tests that say it.
"""

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.rolling import *  # noqa: F401,F403
from agora_runner.rolling import (  # noqa: F401
    RollError,
    RollSpec,
    _archive_header,
    _archived,
    _body,
    _split_title,
    dedup,
    join_bullets,
    join_paragraphs,
    plan,
    run,
    split_bullets,
    verify,
)
