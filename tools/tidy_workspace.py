"""Sweep the scratch files this loop leaves in `/data/workspace`.

Every cycle writes drafts at the workspace root -- `entry.md`, `digest-new.md`,
`live.md`, whatever a shell block needed a file for -- and nothing has ever
removed them. Three cycles have filed this by hand (163 at 68 files, 173 at
~90, 176 again) and each time the fix was a manual sweep that had to be
reinvented, which is why it kept coming back. One line ends it:

    python3 -m tools.tidy_workspace

**What it is allowed to touch is a whitelist of its own naming conventions,
not a blacklist of things to spare.** `/data/workspace` holds nine clones,
this repo among them, so a sweep that decided what to keep would be one bad
predicate away from deleting a checkout with uncommitted work in it. So:

- regular files at the root, and only depth 1, are *moved* into today's
  archive. Never deleted, because a cycle that is still running may be about
  to read one back, and because the loss is silent if it is wrong.
- directories named exactly `_scratch-archive-<YYYY-MM-DD>` -- the shape this
  script itself creates -- are deleted once they are older than the retention
  window. Anything whose name does not parse as that date is left alone.
- directories named `_review-*`, the reviewer worktrees `review-rubric.md`
  says to make, are removed through `git worktree remove` so the metadata in
  the clone goes with them.

A directory it did not create, or a name it cannot parse, is not its business.
That is the whole safety argument, and it is why there is no `--force`.

**Run it at the start of a cycle, not at the end.** Found by running
`--dry-run` while writing this: the reviewer worktree it offered to remove was
the one the second reader was reading out of at that moment, and the drafts it
archives are the files a cycle in its wrap-up is about to write. At the start
of a cycle every one of those belongs to a cycle that is already over.
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess

# Where the loop actually works. Not derived from `__file__`: this script
# lives in a clone *inside* the workspace, so walking up from here would make
# the target depend on how deep the clone happens to be.
WORKSPACE = "/data/workspace"

# The runner clone, for `git worktree remove`. A reviewer worktree is
# registered in the clone it was made from, so deleting the directory alone
# leaves an entry that makes `git worktree list` lie.
_CLONE = os.path.join(WORKSPACE, "agora-persona-runner")

_ARCHIVE_PREFIX = "_scratch-archive-"
_ARCHIVE_NAME = re.compile(r"^_scratch-archive-(\d{4}-\d{2}-\d{2})$")
_REVIEW_NAME = re.compile(r"^_review-")

# A week. Long enough that a cycle which archived something by mistake has
# several days to notice, short enough that the directory count stays legible.
DEFAULT_RETENTION_DAYS = 7


def _today(today=None):
    """Today as `YYYY-MM-DD`, or the caller's stamp unchanged.

    A string rather than a `date` all the way through, because the archive
    directory names *are* strings and round-tripping through a date object
    would let this and `expired_archives` disagree about the format.
    """
    return today or datetime.date.today().isoformat()


def archive_loose_files(root, today, dry_run=False):
    """Move every regular file at `root` into `_scratch-archive-<today>`.

    Returns the names moved, sorted. Moved rather than copied, or the sweep
    that is meant to end the growth becomes the thing doing the growing --
    and moved rather than deleted, because the point of failure here is a
    predicate that turned out to include something that mattered.
    """
    names = sorted(name for name in os.listdir(root)
                   if os.path.isfile(os.path.join(root, name)))
    if not names or dry_run:
        return names
    destination = os.path.join(root, _ARCHIVE_PREFIX + today)
    os.makedirs(destination, exist_ok=True)
    for name in names:
        # Not `shutil.move`: a same-named file from an earlier sweep on the
        # same day would land *inside* the directory it collides with if the
        # destination already exists as one. `os.replace` overwrites, which
        # for two drafts of the same scratch file on the same day is the
        # answer that surprises nobody.
        os.replace(os.path.join(root, name), os.path.join(destination, name))
    return names


def expired_archives(root, today, retention_days):
    """Archive directories older than the window, oldest first.

    Parses the date out of the name rather than reading the mtime: an archive
    directory's mtime moves every time a later sweep writes into it, so a
    directory that has been collecting files all week would read as new.
    A name that does not parse is not returned at all -- this function is the
    only thing standing between the delete below and the rest of the
    workspace.
    """
    cutoff = datetime.date.fromisoformat(today) - datetime.timedelta(
        days=retention_days)
    expired = []
    for name in sorted(os.listdir(root)):
        match = _ARCHIVE_NAME.match(name)
        if not match or not os.path.isdir(os.path.join(root, name)):
            continue
        try:
            stamp = datetime.date.fromisoformat(match.group(1))
        except ValueError:
            # `\d{4}-\d{2}-\d{2}` matches `2026-13-45`. Unparseable is not
            # expired; leaving it costs a directory, guessing costs the files.
            continue
        if stamp < cutoff:
            expired.append(name)
    return expired


def stale_review_worktrees(root):
    """`_review-*` directories left by the reviewer, sorted."""
    return sorted(name for name in os.listdir(root)
                  if _REVIEW_NAME.match(name)
                  and os.path.isdir(os.path.join(root, name)))


def _remove_worktree(root, name, clone):
    """`git worktree remove`, falling back to a plain delete.

    The fallback matters because the two can disagree: a worktree whose clone
    has since been re-cloned is a directory git has never heard of, and
    refusing to remove it would leave exactly the litter this script exists
    for. Either way the directory goes.
    """
    path = os.path.join(root, name)
    if os.path.isdir(clone):
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", path],
                       capture_output=True, check=False)
    if os.path.isdir(path):
        shutil.rmtree(path)
    if os.path.isdir(clone):
        subprocess.run(["git", "-C", clone, "worktree", "prune"],
                       capture_output=True, check=False)


def tidy(root=WORKSPACE, retention_days=DEFAULT_RETENTION_DAYS, dry_run=False,
         today=None, clone=_CLONE):
    """Run the sweep. Returns `(archived, expired, worktrees)`."""
    stamp = _today(today)
    worktrees = stale_review_worktrees(root)
    if not dry_run:
        for name in worktrees:
            _remove_worktree(root, name, clone)
    # After the worktrees, so a `_review-*` directory is never mistaken for a
    # loose file, and before the expiry so today's archive is not itself a
    # candidate for deletion on a zero-day retention.
    archived = archive_loose_files(root, stamp, dry_run=dry_run)
    expired = expired_archives(root, stamp, retention_days)
    if not dry_run:
        for name in expired:
            shutil.rmtree(os.path.join(root, name))
    return archived, expired, worktrees


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=WORKSPACE)
    parser.add_argument("--retention-days", type=int,
                        default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would happen and change nothing")
    args = parser.parse_args(argv)

    archived, expired, worktrees = tidy(
        args.root, args.retention_days, dry_run=args.dry_run)

    verb = "would remove" if args.dry_run else "removed"
    for name in worktrees:
        print("%s reviewer worktree %s" % (verb, name))
    verb = "would archive" if args.dry_run else "archived"
    if archived:
        print("%s %d loose file(s): %s" % (verb, len(archived),
                                           ", ".join(archived)))
    verb = "would delete" if args.dry_run else "deleted"
    for name in expired:
        print("%s %s (older than %d days)" % (verb, name, args.retention_days))
    if not (archived or expired or worktrees):
        print("nothing to tidy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
