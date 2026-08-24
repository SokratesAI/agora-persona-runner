"""Sweep the scratch files this loop leaves in its workspace.

Workspace, plural: this cycle's own `$NOVA_WORKSPACE` and the shared
`/data/workspace` they are cut from, in that order. See `workspace_roots`.

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
import json
import os
import re
import shutil
import subprocess
import time

# Where the loop actually works. Not derived from `__file__`: this script
# lives in a clone *inside* the workspace, so walking up from here would make
# the target depend on how deep the clone happens to be.
#
# **This constant was the shared checkout and only the shared checkout, and
# concurrent cycles made that wrong in both directions.** The bridge exports
# `NOVA_WORKSPACE`, and for a concurrent cycle it is a private worktree at
# `/data/workspace-concurrent/<slot>/` -- every other command in `prompt.md`
# says `${NOVA_WORKSPACE:-/data/workspace}` and this file said the literal
# path. So a cycle running `python3 -m tools.tidy_workspace` from its own
# checkout archived files in, and reported the branches of, a directory it
# does not work in, while its own root was never swept and its own clones
# were never surveyed. Measured Cycle 368: the tool named
# `nova/status-word-back-on-the-card`, which is the *shared* checkout's parked
# branch, to a cycle whose four clones were all detached at `origin/main`.
# Two handoffs read that as a defect in the verdict.
SHARED_WORKSPACE = "/data/workspace"


def workspace_roots(environ=None):
    """The roots to sweep, this cycle's own first, deduplicated.

    Both, not one. Pointing this at `NOVA_WORKSPACE` alone would fix the
    blindness and introduce the mirror of it: the shared checkout is where a
    serialized cycle's leftover branch sits (it is parked on one right now),
    and its `_scratch-archive-*` directories would stop expiring the moment
    cycles went concurrent, which is every cycle. So the shared root stays on
    the list and simply stops being the only thing on it. It is dropped when
    it does not exist, so a box that never had one does not get an error
    every run.
    """
    env = os.environ if environ is None else environ
    roots = []
    for path in (env.get("NOVA_WORKSPACE"), SHARED_WORKSPACE):
        if path and os.path.isdir(path) and path not in roots:
            roots.append(path)
    return roots


# The single-root default for `tidy()` and `survey_checkouts()`, which each
# still take exactly one root -- `main` is what iterates. A sentinel rather
# than a path, because the value read at import is the one the interpreter
# started with: a caller that sets `NOVA_WORKSPACE` and then relies on the
# default would silently get the old one. Reviewer finding on runner#319.
WORKSPACE = None


def _default_root(root):
    return root if root is not None else (
        workspace_roots() or [SHARED_WORKSPACE])[0]


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
    """Every directory under `root` that is a git clone, sorted.

    `.git` is a *file* in a linked worktree -- one line pointing at the real
    git directory -- and every clone a concurrent cycle works in is one of
    those. `os.path.isdir` on it is False, so this returned an empty list for
    a concurrent workspace and the survey reported nothing rather than
    reporting a gap. That is the shape this file's own comments keep naming:
    a check whose negative result was guaranteed in advance. `os.path.exists`
    covers both, and the survey's git commands fail harmlessly on a directory
    that merely happens to contain something called `.git`.

    **`_review-*` is excluded, and widening the predicate is exactly what
    made that necessary.** A reviewer worktree is itself a linked worktree,
    so the moment `.git` stopped having to be a directory these started
    matching -- reproduced by the reviewer on this change: `clones()`
    answered `['_review-c178', '_review-c179', 'main_clone']`, the survey
    printed verdicts for two in-flight review checkouts alongside the real
    clone, and `_remove_worktree` fed them back in as candidate owners and
    had `_review-c178` deregister itself. A review worktree is not a clone
    and never was; the old predicate excluded it by accident, and the
    accident is now a rule.
    """
    return sorted(name for name in os.listdir(root)
                  if not _REVIEW_NAME.match(name)
                  and os.path.exists(os.path.join(root, name, ".git")))


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
# The same two names without the remote, for "is this branch itself a base".
_BASE_BRANCHES = tuple(name.split("/", 1)[1] for name in _BASES)


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


# The content comparison above answers "did this branch's work land" only for
# as long as the base stands still. It does not: `git diff origin/main HEAD` on
# a branch squash-merged six commits ago reports every file main has changed
# *since*, so the branch reads `unfinished` and the file list narrates main's
# own later work as work somebody abandoned. Measured Cycle 372 on the shared
# checkout, parked on `nova/status-word-back-on-the-card`: 14 files, 1,403
# deletions, all of them main's. Cycle 365 read that verdict and treated the
# branch as something to finish; Cycles 366 and 367 read the same line.
#
# Three git-only instruments were tried on that exact branch and all three
# failed, so they are written down rather than left to be retried:
# `git cherry` said `+` (the squash rewrote the patch-id), `git merge-tree`
# conflicted in both files, and reverse-applying the branch's three-dot patch
# to main's tree failed on moved context. The first attempt at that last one
# "succeeded" against the *working tree* -- which is checked out on the branch,
# so it was a guaranteed positive that proved nothing.
#
# GitHub knows the answer and can simply be asked: a merged PR whose head is
# this branch. That is the authoritative fact, not an inference from content.
GH_TIMEOUT_SECONDS = 30


def _merged_pr(root, clone, branch):
    """The newest merged PR whose head branch is `branch`, or None.

    Returns `(pr, checked)`. `checked` is False when `gh` could not answer at
    all -- absent, unauthenticated, timed out -- which is deliberately not the
    same as "no merged PR": one means the branch really is unfinished, the
    other means nobody asked. Reporting the second as the first is how a
    landed branch gets called unfinished, which is the bug this exists for.
    """
    if not branch or branch in ("HEAD",) + _BASE_BRANCHES:
        return None, False
    try:
        done = subprocess.run(
            ["gh", "pr", "list", "--state", "merged", "--head", branch,
             "--limit", "10", "--json", "number,mergedAt,headRefOid"],
            cwd=os.path.join(root, clone), capture_output=True, text=True,
            check=False, timeout=GH_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        return None, False
    if done.returncode != 0:
        return None, False
    try:
        rows = json.loads(done.stdout or "[]")
    except ValueError:
        return None, False
    if not isinstance(rows, list) or not rows:
        return None, True
    # `--limit 1` trusted an ordering `gh` does not document. A branch name
    # this loop reuses -- `nova/<slug>` collides readily -- can carry several
    # merged PRs, and the one that decides this verdict must be the newest,
    # picked here rather than assumed. Reviewer finding on #324.
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None, True
    return max(rows, key=lambda r: r.get("mergedAt") or ""), True


def _tip_contains_merge(root, clone, head_oid):
    """Whether HEAD is exactly the commit GitHub merged, from the graph.

    Returns True (landed), False (there is something on top), or None when the
    question cannot be answered here -- the merged commit is not in this
    clone's object store, which is the ordinary case for a branch deleted on
    the remote after its merge.

    This is the check `_committed_after` should have been. Reviewer finding on
    #324, and the severity was right: deciding "throw this branch away" by
    comparing two wall clocks is wrong in the direction that loses work.
    `git rebase --committer-date-is-author-date`, an unsynced container clock
    and an explicit `GIT_COMMITTER_DATE` all produce a tip that timestamps
    *before* a merge it does not contain.
    """
    if not head_oid:
        return None
    if _git(root, clone, "cat-file", "-e", head_oid + "^{commit}").returncode != 0:
        return None
    tip = _git(root, clone, "rev-parse", "HEAD").stdout.strip()
    if tip == head_oid:
        return True
    # HEAD is a descendant of the merged head: real commits sit on top of it.
    if _git(root, clone, "merge-base", "--is-ancestor", head_oid,
            "HEAD").returncode == 0:
        return False
    # The merged head is not on this branch at all -- a rewritten or
    # unrelated history. Not an answer, and not a licence to guess.
    return None


def _remote_only_commits(root, clone, branch, base, merged_head=None):
    """Commits on `origin/<branch>` that no one has taken and nothing has merged.

    Every other instrument in this file reads the local HEAD, and a branch does
    not live only in the checkout that happens to be parked on it. A cycle that
    pushed and was then killed -- or that pushed a follow-up commit onto a
    branch whose PR had already merged -- leaves work that exists on the remote
    and nowhere else, and `git diff origin/main HEAD` cannot see any of it.

    Returns `(commits, failed)`. `commits` is a list of `<short-oid> <subject>`
    lines, empty both when the remote ref holds nothing outstanding and when
    there is no such ref to ask -- the ordinary case for a branch never pushed.
    `failed` is True when the question could not be *asked*: `git log` itself
    exited non-zero, which a concurrent `fetch --prune` on the shared clone can
    cause at any time.

    Those two are separated because collapsing them is the bug this whole
    function exists to fix, arriving on the error path. An empty list is read
    one line down as "nothing outstanding" and hands back `leftover`, which
    reads "the branch is litter". `files_failed` in this same module already
    carries the rule: reporting a failure as an empty list is reporting a
    failure as success. Reviewer finding on #337, and it had it right -- the
    first version returned a bare `None` for both, and the caller`s `or []`
    threw the distinction away one line after a docstring claimed to keep it.

    The `--not` list is what makes this narrow rather than noisy, and it has
    three entries for three separate reasons:

    - `HEAD`, because the commits of an ordinary merged branch are reachable
      from it, so a branch whose work landed answers empty.
    - `<base>`, because a commit that has since reached main is not
      outstanding however it got there.
    - `merged_head`, the `headRefOid` GitHub reports for the merged PR, because
      **a checkout can simply be behind the head that merged.** Measured Cycle
      384, and it is the case that nearly shipped a false positive here: the
      shared checkout sat at `0e94630` while `origin/` the same branch and PR
      #316's own merged head were both `b1958cb`. Nothing was outstanding; one
      clone was one commit stale. Without this the sweep calls every checkout
      that has not caught up to its own merged branch unfinished forever.
    """
    if not branch or branch in ("HEAD",) + _BASE_BRANCHES or base is None:
        return [], False
    ref = "origin/" + branch
    if _git(root, clone, "rev-parse", "--verify", "--quiet",
            ref).returncode != 0:
        # No such ref. Genuinely nothing to report, not a failure to ask.
        return [], False
    excluded = ["HEAD", base]
    if merged_head and _git(root, clone, "cat-file", "-e",
                            merged_head + "^{commit}").returncode == 0:
        excluded.append(merged_head)
    done = _git(root, clone, "log", "--oneline", ref, "--not", *excluded)
    if done.returncode != 0:
        return [], True
    return [line for line in done.stdout.split("\n") if line.strip()], False


def _committed_after(root, clone, merged_at):
    """True if this branch's tip was committed after `merged_at`.

    The fallback, used only when `_tip_contains_merge` cannot answer from the
    graph. It reads two clocks and is therefore fallible in both directions,
    so a verdict that rests on it is flagged and printed as resting on it.

    Anything this cannot parse answers True, because the safe error is to
    leave a branch marked unfinished.
    """
    if not merged_at:
        return True
    stamp = _git(root, clone, "log", "-1", "--format=%cI", "HEAD").stdout.strip()
    if not stamp:
        return True
    try:
        tip = datetime.datetime.fromisoformat(stamp)
        merged = datetime.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if tip.tzinfo is None or merged.tzinfo is None:
        return True
    return tip > merged


def survey_checkouts(root=WORKSPACE, fetch=True, ask_github=True):
    root = _default_root(root)
    """One verdict per clone, after refreshing its remote refs.

    Returns a list of dicts, one per clone, sorted by name:

    - `clean` -- no uncommitted files and nothing on this branch that the
      base does not already have.
    - `leftover` -- a branch whose content is identical to the base, or whose
      work went in as a merged PR that the branch has nothing on top of. Its
      work has landed; the branch is litter. The first half is the case a
      stale ref hides, the second the case a *moving* base hides. Neither is
      given while `origin/<branch>` holds a commit this checkout does not --
      that is work, and it is only on the remote.
    - `unfinished` -- something here is genuinely not on the base yet, either
      uncommitted, committed, or pushed and present only on the remote. This
      is the one worth a cycle's attention.
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
        if base is not None:
            counted = _git(root, clone, "rev-list", "--count",
                           base + "..HEAD").stdout.strip()
            ahead = int(counted) if counted.isdigit() else 0
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
        # Only for a clean tree. Uncommitted files are unfinished whatever
        # GitHub says about the branch they sit on -- a merged PR says nothing
        # about an edit a cycle was killed halfway through.
        merged_pr = None
        merged_pr_checked = False
        commits_after_merge = False
        rests_on_clock = False
        if verdict == "unfinished" and not dirty and ask_github:
            merged_pr, merged_pr_checked = _merged_pr(root, clone, branch)
            if not merged_pr_checked:
                # One systemic failure -- no `gh`, no auth, no egress -- fails
                # identically for every clone, and each attempt costs up to
                # GH_TIMEOUT_SECONDS on the first thing a cycle runs. Sixteen
                # of those is eight minutes of nothing. Reviewer finding on
                # #324. One "could not ask" answers for the whole sweep.
                ask_github = False
            if merged_pr:
                contains = _tip_contains_merge(root, clone,
                                               merged_pr.get("headRefOid"))
                if contains is None:
                    rests_on_clock = True
                    contains = not _committed_after(root, clone,
                                                    merged_pr.get("mergedAt"))
                if contains:
                    verdict = "leftover"
                else:
                    commits_after_merge = True
        # Both roads to `leftover` -- identical content, and a merged PR the
        # tip has nothing on top of -- are decided entirely from this
        # checkout's HEAD, and `leftover` is a licence to delete the branch.
        # Measured Cycle 384 on the shared checkout, which was parked on
        # `nova/status-word-back-on-the-card`: local ref `0e94630`,
        # `origin/` the same branch `b1958cb`, and that commit is on no other
        # ref in the repository. The sweep printed "its work has landed, the
        # branch is litter" over a pushed commit nothing had merged. That is
        # wrong in the direction that loses work, which is the direction this
        # file has already refused to be wrong in twice.
        remote_only = []
        remote_only_failed = False
        landed_locally = False
        if verdict in ("leftover", "unfinished"):
            remote_only, remote_only_failed = _remote_only_commits(
                root, clone, branch, base,
                merged_head=merged_pr.get("headRefOid") if merged_pr else None)
            if remote_only and verdict == "leftover":
                # Not litter. `unfinished` is the word that means "worth a
                # cycle's attention", and the line below says where the work
                # actually is, because it is not in the files this sweep can
                # list -- `git diff` against a local HEAD cannot see it.
                verdict = "unfinished"
                # And the file list is deliberately not computed for this one.
                # Everything in this checkout demonstrably landed -- that is
                # what the `leftover` this replaces meant -- so `git diff
                # <base> HEAD` here is a list of main's *own* later work, which
                # is the Cycle 372 bug arriving through a new door. Measured on
                # the same checkout: 120 files, none of them the outstanding
                # commit. What is outstanding is on the line above.
                landed_locally = True
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
        if verdict == "unfinished" and base is not None and not landed_locally:
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
                    "dirty": dirty, "ahead": ahead, "verdict": verdict,
                    "fetched": fetched, "files": files,
                    "files_failed": files_failed,
                    "merged_pr": merged_pr.get("number") if merged_pr else None,
                    "merged_pr_checked": merged_pr_checked,
                    "commits_after_merge": commits_after_merge,
                    "rests_on_clock": rests_on_clock,
                    "remote_only": remote_only,
                    "remote_only_failed": remote_only_failed,
                    "landed_locally": landed_locally})
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
    root = _default_root(root)
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
    parser.add_argument("--root", action="append", default=None,
                        help="sweep this root instead of the cycle's own "
                             "workspace and the shared checkout; repeatable")
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
    parser.add_argument("--no-gh", action="store_true",
                        help="do not ask GitHub whether a branch was merged; "
                             "a landed branch then reads `unfinished`")
    args = parser.parse_args(argv)

    roots = args.root or workspace_roots() or [SHARED_WORKSPACE]
    # Only when there is more than one, so the ordinary single-root output
    # every existing caller and test reads is byte-identical.
    for root in roots:
        if len(roots) > 1:
            # Labelled, not just named. A fresh session reading two absolute
            # paths has to infer which is its own from the shape of the path,
            # and the whole point of this change is that a cycle was reading a
            # true report about the wrong directory. Reviewer finding on #319.
            print("== %s (%s)"
                  % (root, "shared" if root == SHARED_WORKSPACE else "yours"))
        _sweep_one(root, args)
    return 0


def _sweep_one(root, args):
    archived, expired, worktrees = tidy(
        root, args.retention_days, dry_run=args.dry_run,
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
    for entry in survey_checkouts(root, fetch=not args.no_fetch,
                                  ask_github=not args.no_gh):
        # Said even for a clone the survey then calls clean, because "clean"
        # off a ref that could not be refreshed is the reassuring answer with
        # nothing behind it -- the shape of failure this whole function was
        # written after.
        if not entry["fetched"] and not args.no_fetch:
            print("%s: could not fetch -- the verdict below is off the refs "
                  "already on disk and may be stale" % (entry["clone"],))
        if entry["verdict"] == "clean":
            continue
        # The verdict word, printed. `prompt.md` step 1c describes this output
        # by the words `leftover` and `unfinished`, the tool printed only
        # prose, and two cycles in a row went looking for the words, found
        # neither and filed the feature as missing. The sentence is the better
        # thing for a human to read, so both are printed rather than one
        # replacing the other.
        if entry["verdict"] == "leftover":
            landed = ("is already merged as #%d" % entry["merged_pr"]
                      if entry["merged_pr"]
                      else "is already on %s" % entry["base"])
            print("%s: [leftover] branch %s %s -- its work has landed, "
                  "the branch is litter" % (entry["clone"], entry["branch"],
                                            landed))
            if entry["rests_on_clock"]:
                # The merged commit is not in this clone, so "nothing was
                # added after the merge" was decided by comparing two clocks
                # rather than by the commit graph. Say so before anyone
                # deletes a branch on the strength of it.
                print("    the merged commit is not in this clone, so that "
                      "rests on the commit date being no later than the "
                      "merge, not on the history")
        elif entry["verdict"] == "no-base":
            print("%s: [no-base] no origin/main or origin/master to compare "
                  "against" % (entry["clone"],))
        elif entry["landed_locally"]:
            # `unfinished`, but not the usual kind, and saying "has work not on
            # origin/main" here would send a cycle looking through this
            # checkout for something that is not in it.
            print("%s: [unfinished] branch %s -- everything in this checkout "
                  "has landed, but the branch is not litter"
                  % (entry["clone"], entry["branch"]))
        else:
            print("%s: [unfinished] branch %s has work not on %s%s"
                  % (entry["clone"], entry["branch"], entry["base"],
                     " (uncommitted)" if entry["dirty"] else ""))
            if entry["commits_after_merge"]:
                print("    #%d merged from this branch, and its tip was "
                      "committed after that -- what is below is the part "
                      "added since" % (entry["merged_pr"],))
            elif not entry["dirty"] and not entry["merged_pr_checked"]:
                print("    could not ask GitHub whether this branch was "
                      "merged, so `unfinished` here may only mean nobody "
                      "asked")
        # Both `unfinished` shapes get these, which is why they sit outside the
        # branch above rather than inside its `else`. A branch can perfectly
        # well hold uncommitted work here *and* a commit only on the remote,
        # and the version of this that lived in the `else` printed the second
        # for one of the two cases.
        if entry["remote_only_failed"]:
            # Printed under `leftover` as well as `unfinished`, and that is the
            # whole point: `leftover` reads "the branch is litter", and the one
            # thing that could have contradicted it is the check that just
            # failed. Same shape as the `rests_on_clock` caveat above -- the
            # verdict stands, because flipping it on any transient git failure
            # would make the sweep cry wolf on lock contention, but nobody gets
            # to read it as though the remote had been consulted.
            print("    could not list what origin/%s carries -- `git log` "
                  "failed, so nothing above rules out a commit sitting on the "
                  "remote" % (entry["branch"],))
        if entry["remote_only"]:
            # Said before the file list, because the file list is about a
            # different place: these commits are on the remote and not in this
            # checkout, so nothing below names them.
            print("    the work is on the remote, not here -- origin/%s "
                  "carries %d commit(s) that neither this checkout nor %s "
                  "has: %s"
                  % (entry["branch"], len(entry["remote_only"]),
                     entry["base"], "; ".join(entry["remote_only"])))
        if entry["files"]:
            print("    files not on %s: %s"
                  % (entry["base"], ", ".join(entry["files"])))
        if entry["files_failed"]:
            print("    could not list which files differ -- `git diff "
                  "--name-only` failed, so the absence of a list above "
                  "says nothing")


if __name__ == "__main__":
    raise SystemExit(main())
