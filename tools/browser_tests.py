"""Run the site's jsdom suite from any checkout, including a concurrent one.

`agora_runner/nova_public/app.js` has no build step -- it is served to the
browser exactly as written -- so `tests/browser/` is the only place its
behaviour is checked at all. Those 399 tests need `jsdom`, which is an
untracked `node_modules` and therefore absent from the private `git
worktree` a concurrent cycle is handed. Cycle 349 hit that, filed it, and
recorded that symlinking the shared `node_modules` did not fix it.

**It did not fix it because it was the wrong directory.** The repo root's
`node_modules` holds express, pino, vitest and typescript, and no jsdom at
all; the browser suite has its own `tests/browser/package.json` and its own
`tests/browser/node_modules` beside it. Measured Cycle 351: link *that* one
into the worktree and all 399 tests pass in 38 seconds. So the handoff item
said every concurrent cycle is blind to JavaScript, and what was actually
true is that one symlink was aimed one level too high.

That is why this is a tool and not a line in the playbook. The knowledge
that decides it is "which of two same-named directories", which is exactly
the kind of thing a cycle re-derives wrongly at 23:30 and then writes into
the permanent record as a capability it does not have.

    python3 -m tools.browser_tests

Exit 0 the suite is green, 1 it is red, 2 the modules could not be
provisioned at all -- and that third case is deliberately not folded into
1, because "the tests failed" and "I never ran the tests" are the two
answers a cycle must never confuse before it merges.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

JSDOM = "jsdom"

# CI bounds this at 5 minutes for the reason build.yaml spells out: the
# failure this suite is prone to is a hung process, not a red test, and two
# runs sat at 33 and 52 minutes before anyone read them as hung. Same bound
# here, for the same reason.
DEFAULT_TIMEOUT = 300


def main_worktree(common_dir: str) -> Path:
    """The checkout that owns the object store, given `--git-common-dir`.

    In a `git worktree` this is the shared checkout rather than ours, which
    is the whole point: the shared one is where a previous cycle's `npm ci`
    actually landed.
    """
    return Path(common_dir).parent


def has_jsdom(modules: Path) -> bool:
    return (modules / JSDOM).is_dir()


def plan(browser_dir: Path, shared_modules: Path | None) -> tuple[str, str]:
    """Decide how to get `jsdom` next to the suite. Pure; the caller acts.

    Returns one of `present` / `link` / `install` with the path it concerns.
    """
    mine = browser_dir / "node_modules"
    if has_jsdom(mine):
        return ("present", str(mine))
    if mine.is_dir() and not mine.is_symlink():
        # A real directory that is missing jsdom is a half-finished install,
        # and linking over it means deleting somebody's files. Reinstall.
        return ("install", str(browser_dir))
    if shared_modules is not None and has_jsdom(shared_modules):
        return ("link", str(shared_modules))
    return ("install", str(browser_dir))


def provision(browser_dir: Path, action: str, target: str) -> None:
    mine = browser_dir / "node_modules"
    if action == "link":
        if mine.is_symlink() or mine.exists():
            mine.unlink()
        os.symlink(target, mine)
        return
    if action == "install":
        subprocess.run(
            ["npm", "ci"], cwd=browser_dir, check=True, timeout=DEFAULT_TIMEOUT
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = ap.parse_args(argv)

    repo = Path(__file__).resolve().parent.parent
    browser_dir = repo / "tests" / "browser"
    if not (browser_dir / "package.json").is_file():
        print(f"no browser suite at {browser_dir}", file=sys.stderr)
        return 2

    shared_modules: Path | None = None
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
        candidate = main_worktree(common) / "tests" / "browser" / "node_modules"
        if candidate != browser_dir / "node_modules":
            shared_modules = candidate
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"could not ask git for the main worktree: {exc}", file=sys.stderr)

    action, target = plan(browser_dir, shared_modules)
    print(f"node_modules: {action} ({target})")
    try:
        provision(browser_dir, action, target)
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"could not provision node_modules: {exc}", file=sys.stderr)
        return 2
    if not has_jsdom(browser_dir / "node_modules"):
        print("jsdom still missing after provisioning", file=sys.stderr)
        return 2

    try:
        proc = subprocess.run(
            ["node", "--test"], cwd=browser_dir, timeout=args.timeout
        )
    except subprocess.TimeoutExpired:
        print(
            f"node --test did not return within {args.timeout}s -- "
            "that is the hung-window failure, not a slow suite",
            file=sys.stderr,
        )
        return 1
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
