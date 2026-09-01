"""Keep a local copy of the journal folder, fetching only what is new.

    python3 -m tools.mirror_journal --dir /data/workspace/journal-mirror

Run it from the **bridge** pod (`Bash`). `/app/bridge/vault_tool.py`
exists only there; the runner pod has no vault client at all, and a
cycle that reaches for this from `terminal_exec` gets `No such file or
directory` (`prompt.md`, "The two shells are not interchangeable").

Four cycles running have been saved by checking a change against the
*real* journal instead of against fixtures tidier than it: Cycle 149's
gap detector would have accused three cycles that had written; Cycle
150's card fix would have corrupted a fourth entry; Cycle 151 found a
broken card nobody had reported; Cycle 152 found that a sixth of the
live entries break a rule its checker was about to enforce. Every one of
those pulled all ~160 documents down one `get` at a time, and the cost
of doing that is the reason it is tempting to skip.

It is one fetch per *new* document instead. A journal entry is written
once and never edited, so a name already on disk is finished content and
re-fetching it can only cost -- which makes the arithmetic here the
whole tool: names in the vault, minus names on disk.

A document that has vanished from the vault is reported, never deleted.
Nothing legitimately removes an entry, so a name that disappears is a
rename or a bad write, and that is a finding for the cycle to look at
rather than something to quietly mirror.
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_journal import JOURNAL_DIR

VAULT_TOOL = "/app/bridge/vault_tool.py"


def plan(local, remote):
    """(what to fetch, what the vault no longer has), both sorted."""
    return sorted(set(remote) - set(local)), sorted(set(local) - set(remote))


def mirror(directory, listing, fetch):
    """Fetch every document `listing` names and `directory` lacks.

    `fetch(name, path)` writes one document and raises on failure. A
    failure leaves that name unfetched and the run reports it -- the
    caller's next run picks it up, because the name is still missing.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    local = [p.name for p in directory.glob("*.md")]
    wanted, orphans = plan(local, listing())

    fetched, failed = [], []
    for name in wanted:
        try:
            fetch(name, directory / name)
        except Exception as exc:  # one bad document must not lose the rest
            failed.append((name, str(exc)))
        else:
            fetched.append(name)
    return fetched, failed, orphans


def _listing(vault_tool, folder):
    def run():
        out = subprocess.run(
            [sys.executable, vault_tool, "ls", folder],
            capture_output=True, text=True, check=True).stdout
        return [line.rsplit("/", 1)[-1] for line in out.splitlines()
                if line.strip().endswith(".md")]
    return run


def _fetcher(vault_tool, folder):
    def run(name, path):
        out = subprocess.run(
            [sys.executable, vault_tool, "get", folder + name],
            capture_output=True, text=True, check=True).stdout
        # `get` prints its own not-found line and still exits 0, so a
        # short body is the only signal that nothing came back. Written
        # via a temp name so a failure never leaves a truncated entry
        # behind, which would look finished to every later run.
        if not out.strip() or out.startswith("[not found"):
            raise RuntimeError(out.strip()[:80] or "empty body")
        tmp = path.with_suffix(".part")
        try:
            tmp.write_text(out)
            tmp.rename(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="/data/workspace/journal-mirror")
    ap.add_argument("--folder", default=JOURNAL_DIR)
    ap.add_argument("--vault-tool", default=VAULT_TOOL)
    args = ap.parse_args(argv)

    folder = args.folder if args.folder.endswith("/") else args.folder + "/"
    fetched, failed, orphans = mirror(
        args.dir, _listing(args.vault_tool, folder),
        _fetcher(args.vault_tool, folder))

    print("fetched %d, already had %d"
          % (len(fetched), len(list(Path(args.dir).glob("*.md"))) - len(fetched)))
    for name in fetched:
        print("  + " + name)
    for name in orphans:
        print("  ? %s is on disk and not in the vault" % name)
    for name, why in failed:
        print("  ! %s: %s" % (name, why))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
