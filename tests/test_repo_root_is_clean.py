"""No cycle scratch may be tracked at the repo root.

`.gitignore` cannot help here: an ignore rule does nothing to a file that
is already tracked, which is exactly how `prompt.md`, `prompt-cur.md` and
`live-new.md` sat on `main` while the list two sections above it grew
three times without ever mentioning them.

`prompt.md` is the one that cost something. It is a stale copy of Nova's
constitution — the real one lives in the vault — and Cycle 220's review
subagent grepped the repo copy, found no mention of the change under
review, and returned a confident finding that the fix had never been
wired in. It had been. A second copy of a document does not have to be
read by a human to do damage.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Everything at the root that is legitimately source. Adding to this list
# is a deliberate act; that is the point of it being a list.
ALLOWED = {
    ".gitignore",
    "Dockerfile",
    "README.md",
    "package.json",
    "pytest.ini",
    "requirements.txt",
    "run.py",
    "run_nova_site.py",
    "tsconfig.json",
    "vitest.config.ts",
}


def tracked_root_files():
    """Tracked paths directly at the repo root, no directories."""
    out = subprocess.run(["git", "ls-files"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    return {p for p in out.splitlines() if p and "/" not in p}


def test_no_unexpected_file_is_tracked_at_the_repo_root():
    stray = sorted(tracked_root_files() - ALLOWED)
    assert not stray, (
        "Cycle scratch is tracked at the repo root: " + ", ".join(stray)
        + ". `.gitignore` cannot remove a file that is already tracked — "
        "`git rm` it. If one of these is genuinely source, add it to ALLOWED "
        "in this test and say why in the commit."
    )


def test_the_allowed_list_has_not_gone_stale():
    """A name left in ALLOWED after its file is gone silently widens the guard."""
    missing = sorted(name for name in ALLOWED if not (REPO / name).exists())
    assert not missing, (
        "ALLOWED names files that no longer exist: " + ", ".join(missing)
        + ". Remove them, or the guard is permanently open for those names."
    )


def test_the_constitution_is_not_copied_into_the_repo():
    """The three documents that live in the vault, named explicitly.

    Distinct from the test above rather than redundant with it: that one
    breaks the moment someone adds a name to ALLOWED, and these three are
    the ones where doing so is a mistake worth naming out loud.
    """
    for name in ("prompt.md", "identity.md", "personality.md"):
        assert not (REPO / name).exists(), (
            f"{name} is a vault document. A copy here is stale by construction "
            "and has already been read as authoritative by a review subagent."
        )
