"""Sweep the scratch files this loop leaves in `/data/workspace`.

Every cycle writes drafts at the workspace root -- `entry.md`, `digest-new.md`,
`live.md`, whatever a shell block needed a file for -- and nothing has ever
removed them. Three cycles have filed this by hand (163 at 68 files, 173 at
~90, 176 again) and each time the fix was a manual sweep that had to be
reinvented, which is why it kept coming back. One line ends it:

    python3 -m tools.tidy_workspace

**What it is allowed to touch is a whitelist of its own naming conventions,
not a blacklist of things to spare.** `/data/workspace` holds eight clones,
this repo among them, so a sweep that decided what to keep would be one bad
predicate away from deleting a checkout with uncommitted work in it. So:

- regular files at the root, and only depth 1, are *moved* into today's
  archive. Never deleted, because a cycle that is still running may be about
  to read one back, and because the loss is silent if it is wrong.
- directories named exactly `_scratch-archive-<YYYY-MM-DD>` -- the shape this
  script itself creates -- are deleted once they are older than the retention
  window. Anything whose name does not parse as that date is left alone.
- directories named `_review-*`, the reviewer worktrees `review-rubric.md`
  says to make, are removed through `git worktree remove` -- offered to every
  clone, so whichever one registered it deregisters it -- but only once they
  have been untouched for `MIN_WORKTREE_AGE_HOURS`.

A directory it did not create, or a name it cannot parse, is not its business.
That is the whole safety argument, and it is why there is no `--force`.

**Run it at the start of a cycle, not at the end**, and note that this is now
advice rather than the only thing standing between a mistimed run and a
reviewer's checkout vanishing mid-read. Found by running `--dry-run` while
writing this: the worktree it offered to remove was the one the second reader
was reading out of at that moment. The age threshold on `_review-*` is the
mechanical half of the same rule, added after that reader pointed out that a
prose convention with no backstop is exactly what `review-rubric.md` exists to
stop being acceptable. The drafts it archives still want the start of a cycle,
since a cycle in its wrap-up is about to write them.
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess
import time

# Where the loop actually works. Not derived from `__file__`: this script
# lives in a clone *inside* the workspace, so walking up from here would make
# the target depend on how deep the clone happens to be.
WORKSPACE = "/data/workspace"

# A reviewer worktree is registered in the clone it was made from, so
# deleting the directory alone leaves an entry that makes `git worktree list`
# lie. Which clone that is cannot be assumed: `review-rubric.md` says to make
# the worktree from inside the repo under review, and cycles routinely open
# PRs against the bridge and the config repos in the same hour. So every
# clone in the workspace is offered the removal and the one that owns it
# takes it -- hardcoding the runner orphaned the registration for every
# other repo while still deleting the directory, which looked like success.
# Second reader on this change.

_ARCHIVE_PREFIX = "_scratch-archive-"
_ARCHIVE_NAME = re.compile(r"^_scratch-archive-(\d{4}-\d{2}-\d{2})$")
_REVIEW_NAME = re.compile(r"^_review-")

# A week. Long enough that a cycle which archived something by mistake has
# several days to notice, short enough that the directory count stays legible.
DEFAULT_RETENTION_DAYS = 7

# A cycle is at most an hour, so a reviewer worktree untouched for four is one
# no live cycle is reading. See `stale_review_worktrees` for why this is a
# threshold rather than the prose instruction it started as.
MIN_WORKTREE_AGE_HOURS = 4


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
        # `os.replace` rather than `shutil.move` for one narrow reason:
        # both are handed the full destination *path*, so neither can nest --
        # the second reader checked, and the two are interchangeable here.
        # What `os.replace` guarantees and `shutil.move` does not promise is
        # the overwrite when a second sweep on the same day meets a file it
        # already archived, which is the case the test below pins.
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


def stale_review_worktrees(root, min_age_hours=MIN_WORKTREE_AGE_HOURS, now=None):
    """`_review-*` directories older than `min_age_hours`, sorted.

    **The age is the mechanical part of "run this at the start of a cycle",
    and it exists because the prose version was not enough.** The second
    reader on this change was itself running out of `_review-c178-tidy` while
    `_review-c178` sat beside it, live, at a different commit for a different
    open PR in the same cycle -- and the first version of this function would
    have force-removed both. `--force` also bypasses git's own refusal to
    remove a worktree with uncommitted changes, so there was nothing left
    between a mistimed run and a reviewer's checkout disappearing mid-read.

    A cycle is at most an hour, so a worktree untouched for four is one no
    live cycle is using. That is a guess about a duration rather than a
    proof, which is why it is a named constant and an argument.
    """
    cutoff = (now or time.time()) - min_age_hours * 3600
    stale = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not _REVIEW_NAME.match(name) or not os.path.isdir(path):
            continue
        if os.path.getmtime(path) <= cutoff:
            stale.append(name)
    return stale


def clones(root):
    """Every directory under `root` that is a git clone, sorted."""
    return sorted(name for name in os.listdir(root)
                  if os.path.isdir(os.path.join(root, name, ".git")))


# The branch a squash merge leaves behind, which is the thing this survey
# exists to name.
#
# `prompt.md` step 1c tells every cycle to sweep `/data/workspace` for work a
# previous cycle left unfinished, and the sweep it describes is `git status`
# plus `git log origin/main..HEAD`. Both of those compare against a *local*
# ref. Nothing in that sweep fetches, and these clones are written to by a
# cycle that opens a PR and then never touches the checkout again -- so
# `origin/main` in a clone can be days behind the real one, and a branch whose
# work merged long ago still reads "2 commits ahead of main, nothing open".
#
# Cycle 208 spent six minutes acting on exactly that: it re-ran a suite,
# opened agora#59, and only found out it was byte-identical to `origin/main`
# because a board row said the work had shipped. The second check agreed with
# the first and was guaranteed to -- `gh pr list --head <branch>` lists open
# PRs, so the merged one that proved the branch was finished is precisely the
# one it cannot return. Two instruments, one answer, neither able to produce
# the other answer: the Cycle 53 rule, in a new place.
#
# So the fetch is not a nicety here, it is the whole measurement, and the
# verdict below is drawn from content rather than from commit counts. A squash
# merge rewrites the commits, so "how many commits is this branch ahead" says
# `2` for merged work and unmerged work alike; `git diff <base> HEAD` says
# nothing at all for the merged one. That difference is the only reliable
# signal, and it is what separates `leftover` from `unfinished`.
_BASES = ("origin/main", "origin/master")


def _git(root, clone, *args):
    """Run git in one clone, capturing output and never raising."""
    return subprocess.run(["git", "-C", os.path.join(root, clone), *args],
                          capture_output=True, text=True, check=False)


def survey_checkouts(root=WORKSPACE, fetch=True):
    """One verdict per clone, after refreshing its remote refs.

    Returns a list of dicts, one per clone, sorted by name:

    - `clean` -- no uncommitted files and nothing on this branch that the
      base does not already have.
    - `leftover` -- a branch whose content is identical to the base. Its work
      has landed; the branch is litter. This is the case a stale ref hides.
    - `unfinished` -- something here is genuinely not on the base yet, either
      uncommitted or committed. This is the one worth a cycle's attention.
    - `no-base` -- neither `origin/main` nor `origin/master` resolves, so
      there is nothing to compare against and this says so rather than
      guessing.

    `fetch=False` is for a caller that has already fetched, and for the test
    that pins what the fetch is worth -- with a stale ref the same clone reads
    `unfinished`, and that divergence is the bug this function removes.
    """
    out = []
    for clone in clones(root):
        if fetch:
            _git(root, clone, "fetch", "--quiet", "--prune", "origin")
        base = next(
            (candidate for candidate in _BASES
             if _git(root, clone, "rev-parse", "--verify", "--quiet",
                     candidate).returncode == 0),
            None)
        branch = _git(root, clone, "rev-parse", "--abbrev-ref",
                      "HEAD").stdout.strip()
        dirty = bool(_git(root, clone, "status", "--porcelain").stdout.strip())
        if base is None:
            verdict = "no-base"
        elif dirty:
            verdict = "unfinished"
        elif _git(root, clone, "diff", "--quiet", base, "HEAD").returncode == 0:
            # Identical content. On the base's own branch that is the ordinary
            # up-to-date checkout; on any other branch it is a leftover.
            verdict = "clean" if branch == base.split("/", 1)[1] else "leftover"
        else:
            verdict = "unfinished"
        out.append({"clone": clone, "branch": branch, "base": base,
                    "dirty": dirty, "verdict": verdict})
    return out


def _remove_worktree(root, name, clone_names):
    """`git worktree remove` from whichever clone owns it, then a plain delete.

    Returns the clone that claimed it, or `None` if none did. The fallback
    matters because the two can disagree: a worktree whose clone has since
    been re-cloned is a directory git has never heard of, and refusing to
    remove it would leave exactly the litter this script exists for. Either
    way the directory goes -- but which of the two happened is now in the
    return value and in what the CLI prints, because "removed" covering both
    a clean deregistration and a silent orphaning is a real distinction
    erased from the only output anyone sees.
    """
    path = os.path.join(root, name)
    owner = None
    for clone in clone_names:
        clone_path = os.path.join(root, clone)
        done = subprocess.run(
            ["git", "-C", clone_path, "worktree", "remove", "--force", path],
            capture_output=True, check=False)
        if done.returncode == 0:
            owner = clone
            break
    if os.path.isdir(path):
        shutil.rmtree(path)
    for clone in clone_names:
        subprocess.run(["git", "-C", os.path.join(root, clone), "worktree", "prune"],
                       capture_output=True, check=False)
    return owner


def tidy(root=WORKSPACE, retention_days=DEFAULT_RETENTION_DAYS, dry_run=False,
         today=None, min_age_hours=MIN_WORKTREE_AGE_HOURS, now=None):
    """Run the sweep. Returns `(archived, expired, worktrees)`."""
    stamp = _today(today)
    worktrees = stale_review_worktrees(root, min_age_hours, now)
    owners = {}
    if not dry_run:
        clone_names = clones(root)
        for name in worktrees:
            owners[name] = _remove_worktree(root, name, clone_names)
    tidy.last_owners = owners
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
    parser.add_argument("--min-worktree-age-hours", type=float,
                        default=MIN_WORKTREE_AGE_HOURS,
                        help="leave reviewer worktrees newer than this alone")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would happen and change nothing")
    parser.add_argument("--no-fetch", action="store_true",
                        help="survey checkouts against the refs already on "
                             "disk; the verdicts are only as fresh as they are")
    args = parser.parse_args(argv)

    archived, expired, worktrees = tidy(
        args.root, args.retention_days, dry_run=args.dry_run,
        min_age_hours=args.min_worktree_age_hours)

    owners = getattr(tidy, "last_owners", {})
    for name in worktrees:
        if args.dry_run:
            print("would remove reviewer worktree %s" % (name,))
        elif owners.get(name):
            print("removed reviewer worktree %s (deregistered from %s)"
                  % (name, owners[name]))
        else:
            print("deleted reviewer worktree %s (no clone claimed it)" % (name,))
    verb = "would archive" if args.dry_run else "archived"
    if archived:
        print("%s %d loose file(s): %s" % (verb, len(archived),
                                           ", ".join(archived)))
    verb = "would delete" if args.dry_run else "deleted"
    for name in expired:
        print("%s %s (older than %d days)" % (verb, name, args.retention_days))
    if not (archived or expired or worktrees):
        print("nothing to tidy")

    # Printed after the sweep and never gated on it: a cycle that had nothing
    # to tidy is exactly the cycle that most needs to be told a checkout is
    # holding unfinished work, and "nothing to tidy" above it reads like an
    # all-clear for the whole workspace.
    for entry in survey_checkouts(args.root, fetch=not args.no_fetch):
        if entry["verdict"] == "clean":
            continue
        if entry["verdict"] == "leftover":
            print("%s: branch %s is already on %s -- its work has landed, "
                  "the branch is litter" % (entry["clone"], entry["branch"],
                                            entry["base"]))
        elif entry["verdict"] == "no-base":
            print("%s: no origin/main or origin/master to compare against"
                  % (entry["clone"],))
        else:
            print("%s: branch %s has work not on %s%s"
                  % (entry["clone"], entry["branch"], entry["base"],
                     " (uncommitted)" if entry["dirty"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
