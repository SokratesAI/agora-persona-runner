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

One thing here does write to a checkout rather than to a name of its own:
`fast_forward_stale` advances a clone that is clean and behind onto its base,
because under concurrency nothing else ever updates the shared checkout every
worktree is cut from. It carries its own safety argument at the function, and
it is the same one -- a fast-forward of a clean tree loses nothing and is
reversible in one command, and every other shape is skipped rather than
merged.

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


# The one call here that leaves the box. Every other git command in this file
# reads local disk and returns instantly; `fetch` talks to GitHub, and this
# runs as the *first* thing a cycle does, before it has picked anything. A
# fetch that hangs -- a dead tailnet, a half-open socket -- would take the
# whole hour with it and the cycle would never say why. So it is bounded, and
# a timeout is treated as a failed command rather than raised: the survey
# still answers, off the refs it has, which is exactly the stale-ref case it
# was written to warn about. The verdict is then no worse than what every
# cycle before this one worked from.
GIT_TIMEOUT_SECONDS = 30


class _Failed:
    """What a git command that could not run looks like to the caller."""

    returncode = 1
    stdout = ""
    stderr = ""


def _git(root, clone, *args):
    """Run git in one clone, capturing output and never raising."""
    try:
        return subprocess.run(["git", "-C", os.path.join(root, clone), *args],
                              capture_output=True, text=True, check=False,
                              timeout=GIT_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError) as e:
        log = "%s: git %s did not complete (%s)" % (clone, " ".join(args), e)
        print(log)
        return _Failed()


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
        fetched = not fetch
        if fetch:
            # Checked, not fired and forgotten. A fetch can fail without
            # hanging -- an unreachable host exits 128 in well under a second
            # -- and the survey would then go on to answer off whatever refs
            # are on disk, which is precisely the stale-ref bug this function
            # exists to remove, silently reintroduced. The verdict below is
            # still the best available answer, so it is still computed; what
            # changes is that the caller is told it may be the old wrong one.
            # Second reader on this change.
            done = _git(root, clone, "fetch", "--quiet", "--prune", "origin")
            fetched = done.returncode == 0
        base = next(
            (candidate for candidate in _BASES
             if _git(root, clone, "rev-parse", "--verify", "--quiet",
                     candidate).returncode == 0),
            None)
        branch = _git(root, clone, "rev-parse", "--abbrev-ref",
                      "HEAD").stdout.strip()
        dirty = bool(_git(root, clone, "status", "--porcelain").stdout.strip())
        ahead = 0
        behind = 0
        if base is not None:
            counted = _git(root, clone, "rev-list", "--count",
                           base + "..HEAD").stdout.strip()
            ahead = int(counted) if counted.isdigit() else 0
            counted = _git(root, clone, "rev-list", "--count",
                           "HEAD.." + base).stdout.strip()
            behind = int(counted) if counted.isdigit() else 0
        if base is None:
            verdict = "no-base"
        elif dirty:
            verdict = "unfinished"
        elif ahead == 0:
            # Nothing here the base has not got. A clone that is merely
            # *behind* lands here too, and that is the answer: `git diff` is
            # non-empty in both directions, so judging on content alone called
            # three untouched clones unfinished on the first real run --
            # `yoyo-evolve` is 470 commits behind with nothing local. The
            # question this sweep asks is "did a cycle leave work here", and
            # being behind is not work.
            verdict = "clean"
        elif _git(root, clone, "diff", "--quiet", base, "HEAD").returncode == 0:
            # Commits the base does not have, and yet no difference in
            # content: the squash merge. This is the pair that matters, and
            # neither half decides it alone -- the count alone says the same
            # thing for landed and unlanded work, and the content alone
            # cannot tell ahead from behind.
            verdict = "leftover"
        else:
            verdict = "unfinished"
        # Which files, not just that there are some. "has work not on
        # origin/main" is true of a half-finished feature and equally true of
        # a `-config` clone whose only delta is a stale `image:` digest --
        # where the "work" is a rollback of the running deployment, and
        # pushing it deploys an old image. Cycle 255 read that verdict on
        # `agora-persona-runner-config`, and the thing that separated the two
        # cases was one `git diff --name-only`. So the sweep runs it, rather
        # than leaving every reader to remember to.
        files = []
        files_failed = False
        if verdict == "unfinished" and base is not None:
            found = set()
            # Both halves, because `unfinished` is reached two ways and the
            # motivating case can arrive by either. A stale `image:` digest in
            # a `-config` clone is a committed delta when a cycle committed it
            # and an uncommitted one when a cycle was killed mid-edit, and the
            # `dirty` branch of the verdict above does not require `ahead > 0`
            # at all -- so a committed-only file list is empty for exactly the
            # clone that most needs one. Reviewer finding on #238.
            names = (_git(root, clone, "diff", "--name-only", base, "HEAD")
                     if ahead else None)
            if names is None:
                # `ahead == 0` and dirty: there is nothing committed here that
                # the base has not got, so `git diff base HEAD` lists the
                # base's *own* newer files, in the other direction. Collecting
                # them would narrate main's work as work somebody left, which
                # is the bug the `clean` verdict exists to avoid.
                pass
            elif names.returncode != 0:
                # Not folded into the empty list. On this code path a
                # difference has already been proven to exist, so "no files"
                # cannot legitimately happen -- reporting the failure as an
                # empty list is a failure reported as success, and the caller
                # would print the old undifferentiated sentence and nothing
                # else. Reviewer finding on #238.
                files_failed = True
            else:
                found.update(line for line in names.stdout.split("\n")
                             if line.strip())
            if dirty:
                # `--porcelain` is `XY <path>`, and a rename is `XY <old> -> <new>`.
                # Only the path is wanted, and only the current name of it.
                for line in _git(root, clone, "status",
                                 "--porcelain").stdout.split("\n"):
                    path = line[3:].strip()
                    if " -> " in path:
                        path = path.split(" -> ", 1)[1]
                    if path:
                        found.add(path)
            files = sorted(found)
        out.append({"clone": clone, "branch": branch, "base": base,
                    "dirty": dirty, "ahead": ahead, "behind": behind,
                    "verdict": verdict,
                    "fetched": fetched, "files": files,
                    "files_failed": files_failed})
    return out


# Nothing in this loop was ever told to update `/data/workspace`. Step 3's
# `git pull` runs in the *cycle's own* workspace, which under concurrency is a
# private worktree, and `prompt.md` forbids a concurrent cycle from writing to
# the shared checkout at all -- so the shared clone moves forward only when a
# serialized cycle happens to run. At 72 minutes one always did. From 18:00 on
# 2026-08-24 the cadence is 18 minutes and every cycle is concurrent, so the
# shared clone can stand still indefinitely while every worktree cut from it
# inherits that commit. Cycle 355 hit the sharp end of this: the running code
# in its worktree was two commits behind the ledger format it had just written
# to the vault, which reads new state as "held by cycle N forever". It
# fast-forwarded by hand -- four commands nobody is instructed to run.
#
# So this is deliberately narrow, because a fast-forward of somebody else's
# checkout is only obviously safe in one shape:
#
#   - the survey called it `clean`: no uncommitted files, nothing committed
#     here that the base has not got, so there is no work to lose;
#   - `HEAD` is the base's own branch by name, not a detached head and not a
#     feature branch that merely happens to be behind -- those are somebody's
#     position, not staleness;
#   - the fetch that produced `base` actually succeeded, or "behind" is a
#     reading off refs from an unknown time ago;
#   - `--ff-only`, so git itself refuses anything that is not a straight
#     replay. The tool never resolves a conflict and never merges.
#
# It is reversible in one command (`git reset --hard <the sha printed>`), and
# the sha before the move is printed for exactly that reason.
def fast_forward_stale(root, entries, dry_run=False):
    """Fast-forward every clean-and-behind clone onto its base.

    Takes the survey rather than re-running it: the survey has already
    fetched, and a second fetch here would be a different, later answer than
    the verdicts being acted on. Returns one dict per clone considered,
    `{clone, base, behind, was, moved, error}`; `moved` is False with `error`
    set when git refused.
    """
    moves = []
    for entry in entries:
        base = entry["base"]
        if base is None or not entry["fetched"]:
            continue
        if entry["verdict"] != "clean" or entry["behind"] < 1:
            continue
        # `origin/main` -> `main`. A clone sitting on a detached HEAD reports
        # the branch as `HEAD`, which matches no base and is skipped, which is
        # what a concurrent cycle's own worktree looks like.
        if entry["branch"] != base.split("/", 1)[-1]:
            continue
        was = _git(root, entry["clone"], "rev-parse", "HEAD").stdout.strip()
        move = {"clone": entry["clone"], "base": base,
                "behind": entry["behind"], "was": was, "moved": False,
                "error": None}
        if not was:
            # The sha is the whole reversibility argument -- it is the one
            # argument to the `git reset --hard` that undoes this. `_git`
            # answers an empty stdout when the command could not run at all,
            # so moving here would be moving a checkout while printing no way
            # back. Refuse instead, and say why.
            move["error"] = "could not read HEAD, so nothing to reverse to"
            moves.append(move)
            continue
        if dry_run:
            moves.append(move)
            continue
        done = _git(root, entry["clone"], "merge", "--ff-only", base)
        if done.returncode == 0:
            move["moved"] = True
        else:
            # Reported, not swallowed. The expected failure under concurrency
            # is another cycle holding the index lock a few milliseconds
            # earlier, which is harmless and self-correcting -- but a clone
            # that silently never advances is the whole bug this function
            # exists to remove, so the caller is told either way.
            move["error"] = (done.stderr or done.stdout or "").strip().split(
                "\n")[-1] or "git merge --ff-only failed"
        moves.append(move)
    return moves


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
    survey = survey_checkouts(args.root, fetch=not args.no_fetch)
    for move in fast_forward_stale(args.root, survey, dry_run=args.dry_run):
        if args.dry_run:
            print("would fast-forward %s to %s (%d commit(s) behind)"
                  % (move["clone"], move["base"], move["behind"]))
        elif move["moved"]:
            print("fast-forwarded %s to %s (%d commit(s); was %s)"
                  % (move["clone"], move["base"], move["behind"],
                     move["was"][:7]))
        else:
            print("%s: %d commit(s) behind %s and could not fast-forward -- %s"
                  % (move["clone"], move["behind"], move["base"],
                     move["error"]))
    for entry in survey:
        # Said even for a clone the survey then calls clean, because "clean"
        # off a ref that could not be refreshed is the reassuring answer with
        # nothing behind it -- the shape of failure this whole function was
        # written after.
        if not entry["fetched"] and not args.no_fetch:
            print("%s: could not fetch -- the verdict below is off the refs "
                  "already on disk and may be stale" % (entry["clone"],))
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
            if entry["files"]:
                print("    files not on %s: %s"
                      % (entry["base"], ", ".join(entry["files"])))
            if entry["files_failed"]:
                print("    could not list which files differ -- `git diff "
                      "--name-only` failed, so the absence of a list above "
                      "says nothing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
