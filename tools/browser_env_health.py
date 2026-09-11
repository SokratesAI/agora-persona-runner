"""Is the browser environment every visual check depends on still there?

Cycle 1210, working idea #248. `/data/workspace/nova-browser` was then ~1.2 GB
of downloaded Chromium, an unpacked Debian sysroot, fonts and `node_modules`,
in no image and no repo: untracked bytes on a volume. Since
agora-claude-bridge#113 the bridge image carries it at `/opt/nova-browser`
and the volume copy is gone (Cycle 1430); this still checks whichever root
`see_page.browser_root()` names. `tools/browser/bootstrap.sh` can rebuild it -- runner#857 made that
script trustworthy and proved it end to end -- but **nothing looks at whether
the environment is still there**, and nothing runs the rebuild.

    python3 -m tools.browser_env_health

So the failure this closes is the one the row names: *"if that volume is lost
or a cycle tidies the directory, every browser-verification capability I have
disappears with it and the next cycle will not even know what it is missing."*
Today a cycle finds out by reaching for `tools.see_page` in the middle of
doing something else, which is the worst moment to discover a three-minute
rebuild. `see_page` names the missing step well (runner#865); what it cannot
do is say so *before* a cycle has committed to a plan that needs it.

**It re-uses `see_page.missing_pieces` rather than re-listing the four
pieces.** A second copy of that list is a second thing to keep in step with
`bootstrap.sh`, and the copy that is not the one `render_env` actually raises
on is the copy that goes stale silently. The rule lives in one place; this
asks it.

**Absent and incomplete are separate verdicts on purpose**, the same call
`heartbeat_health` makes on OFF versus OVERDUE. A root that is not there at
all means the volume was lost or never built and the whole 1.2 GB has to come
down again; a root that is there and missing a piece means a bootstrap was
interrupted, which is a different story about what happened and reads
differently to whoever finds it. Both are exit 2 and both print the same
rebuild command, because the fix happens to be the same one -- but a check
that merged them would be reporting a cause it did not measure.

**What it does not judge, and says so on every run:** whether the environment
actually *renders*. Every piece can be present and Chromium can still fail to
start -- a missing shared library the sysroot did not carry, a font cache that
never built. That needs a real page and a real network, which is `see_page`
itself; this is a file-level check that costs milliseconds, which is why it
can run on every sweep.

Exit 0 when the environment is complete. Exit 2 when it is absent or
incomplete. Exit 1 when the root could not be read at all -- a path that is
not a directory, or one this pod may not stat -- because a check that could
not run must never read as a check that came back clean.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.see_page import browser_root, missing_pieces  # noqa: E402

REBUILD = "tools/browser/bootstrap.sh"


def verdict(root: Path) -> tuple:
    """`(status, headline, detail lines)` for this root.

    `status` is the exit code. Whether the root is there at all is settled
    before `missing_pieces` is asked anything, because that rule answers "all
    four are missing" for an absent root and for an empty one alike -- it is
    a question about the contents of a directory, so it cannot tell a volume
    that was lost from a bootstrap that stopped after `mkdir`.
    """
    try:
        there = root.is_dir()
    except OSError as exc:
        return 1, f"CANNOT READ {root} — {exc.__class__.__name__}: {exc}", []
    if not there:
        if root.exists():
            return 1, f"CANNOT READ {root} — it exists and is not a directory", []
        return (
            2,
            f"BROWSER ENVIRONMENT ABSENT — nothing at {root}",
            [
                "the volume was lost, or this pod has never had one",
                f"rebuild: {REBUILD} (~3 minutes, ~1.5GB, no root needed)",
            ],
        )
    try:
        gone = missing_pieces(root)
    except OSError as exc:
        return 1, f"CANNOT READ {root} — {exc.__class__.__name__}: {exc}", []
    if gone:
        return (
            2,
            f"BROWSER ENVIRONMENT INCOMPLETE — {root} exists and a bootstrap did not finish",
            list(gone) + [f"rebuild: {REBUILD} (~3 minutes, ~1.5GB, no root needed)"],
        )
    return 0, "", []


def required_count() -> int:
    """How many pieces `missing_pieces` looks for, asked rather than counted.

    Writing `4` here would be the re-listing this module exists not to do: a
    fifth piece added to `bootstrap.sh` and to `missing_pieces` would leave
    this line saying four forever, and nothing would fail. An empty directory
    is missing every piece by construction, so the length of that answer is
    the size of the rule.
    """
    with tempfile.TemporaryDirectory() as empty:
        return len(missing_pieces(Path(empty)))


def main() -> int:
    root = browser_root()
    status, headline, detail = verdict(root)
    if headline:
        print(headline)
        for line in detail:
            print(f"    {line}")
    print(
        f"Read the browser environment at {root} — the {required_count()} "
        "piece(s) `tools.see_page.render_env` refuses to run without, asked "
        "of that module rather than re-listed here."
    )
    print(
        "    NOT JUDGED  whether it renders. Every piece can be present and "
        "Chromium still fail to start; that needs a real page, which is "
        "tools.see_page."
    )
    return status


if __name__ == "__main__":
    sys.exit(main())
