"""The workspace sweep only ever touches names it made itself.

`/data/workspace` holds nine git clones, this repo among them, and the sweep
runs unattended at the end of a cycle. So the tests that matter most here are
the ones asserting what it leaves alone -- a clone with uncommitted work in it
is not recoverable from anywhere, and no amount of correct archiving would pay
for deleting one.

Every test runs against a `tmp_path`, never the real workspace.
"""
import json
import os
import subprocess
import time
import types

import pytest

from tools import tidy_workspace


@pytest.fixture
def workspace(tmp_path):
    """A workspace shaped like the real one: clones, tool dirs, scratch."""
    (tmp_path / "entry.md").write_text("draft", encoding="utf-8")
    (tmp_path / "digest-new.md").write_text("draft", encoding="utf-8")
    for clone in ("agora-persona-runner", "agora-claude-bridge", "platform-config"):
        (tmp_path / clone / ".git").mkdir(parents=True)
        (tmp_path / clone / "uncommitted.py").write_text("work", encoding="utf-8")
    for tool_dir in ("journal-mirror", "node_modules", "vault-orphans"):
        (tmp_path / tool_dir).mkdir()
        (tmp_path / tool_dir / "cached").write_text("x", encoding="utf-8")
    return tmp_path


def _names(root):
    return sorted(os.listdir(root))


def test_loose_files_are_archived_and_nothing_else_moves(workspace):
    archived, expired, worktrees = tidy_workspace.tidy(
        str(workspace), today="2026-08-14")

    assert archived == ["digest-new.md", "entry.md"]
    assert (expired, worktrees) == ([], [])
    assert not (workspace / "entry.md").exists()
    assert (workspace / "_scratch-archive-2026-08-14" / "entry.md").read_text() \
        == "draft"


@pytest.mark.parametrize("survivor", [
    "agora-persona-runner", "agora-claude-bridge", "platform-config",
    "journal-mirror", "node_modules", "vault-orphans",
])
def test_a_directory_it_did_not_create_is_never_touched(workspace, survivor):
    """The one that would cost more than everything else here saves. A clone
    holds work that exists nowhere else -- `/data/workspace` is where a
    killed cycle's unfinished feature was once found, complete and green,
    days later.

    Parametrised over every directory shape the real workspace has, rather
    than asserting a count, so a future predicate that starts matching one of
    them names which one.
    """
    tidy_workspace.tidy(str(workspace), today="2026-08-14")

    assert (workspace / survivor).is_dir()
    assert _names(workspace / survivor)


def test_only_archives_past_the_window_are_deleted(workspace):
    """Retention is a boundary, so both sides of it are asserted: the day
    before the cutoff goes and the cutoff day itself stays. A test that only
    checked the old one passes with an off-by-one that deletes a week early.
    """
    for day in ("2026-08-01", "2026-08-06", "2026-08-07", "2026-08-13"):
        (workspace / ("_scratch-archive-" + day)).mkdir()
        (workspace / ("_scratch-archive-" + day) / "old.md").write_text(
            "x", encoding="utf-8")

    _, expired, _ = tidy_workspace.tidy(
        str(workspace), retention_days=7, today="2026-08-14")

    assert expired == ["_scratch-archive-2026-08-01", "_scratch-archive-2026-08-06"]
    assert not (workspace / "_scratch-archive-2026-08-01").exists()
    assert (workspace / "_scratch-archive-2026-08-07").is_dir()
    assert (workspace / "_scratch-archive-2026-08-13").is_dir()


def test_todays_own_archive_survives_its_own_sweep(workspace):
    """Zero retention is the setting that would eat the files this run just
    archived, one line after writing them. Expiry runs after archiving, so
    today's directory has to be newer than the cutoff rather than equal to
    it, and `<` is what makes that true.
    """
    archived, expired, _ = tidy_workspace.tidy(
        str(workspace), retention_days=0, today="2026-08-14")

    assert archived == ["digest-new.md", "entry.md"]
    assert expired == []
    assert (workspace / "_scratch-archive-2026-08-14" / "entry.md").exists()


def test_a_directory_that_only_looks_like_an_archive_is_left_alone(workspace):
    """The predicate is the whole safety argument, so it is tested against
    the near misses rather than only against a clone. An unparseable date is
    deliberately kept: leaving it costs one directory, guessing costs files.
    """
    for name in ("_scratch-archive-2026-13-45", "_scratch-archive-old",
                 "_scratch-archive-2026-08-01-notes", "scratch-archive-2026-08-01"):
        (workspace / name).mkdir()

    _, expired, _ = tidy_workspace.tidy(
        str(workspace), retention_days=7, today="2026-08-14")

    assert expired == []
    for name in ("_scratch-archive-2026-13-45", "_scratch-archive-old",
                 "_scratch-archive-2026-08-01-notes", "scratch-archive-2026-08-01"):
        assert (workspace / name).is_dir(), name


def test_an_old_reviewer_worktree_is_removed(workspace):
    """`review-rubric.md` says to make one every build cycle and to remove it
    afterwards; four were still on disk when this script was written, which
    is the measurement that says the manual step does not happen.
    """
    (workspace / "_review-c178").mkdir()
    (workspace / "_review-c178" / "checkout.py").write_text("x", encoding="utf-8")
    old = time.time() - 24 * 3600
    os.utime(workspace / "_review-c178", (old, old))

    _, _, worktrees = tidy_workspace.tidy(str(workspace), today="2026-08-14")

    assert worktrees == ["_review-c178"]
    assert not (workspace / "_review-c178").exists()


def test_a_live_reviewer_worktree_is_left_alone(workspace):
    """The finding that made this threshold exist, reproduced. While the
    second reader on this very change was working, two `_review-c178*`
    worktrees sat side by side -- one it was reading out of, one live at a
    different commit for a different open PR in the same cycle. The first
    version of this script would have force-removed both, and `--force`
    bypasses git's own refusal to drop a worktree with uncommitted changes.

    Age is what tells them apart without asking the caller to have timed the
    run correctly, so this is the test the prose instruction could not be.
    """
    for name in ("_review-c178", "_review-c178-tidy"):
        (workspace / name).mkdir()
        (workspace / name / "checkout.py").write_text("x", encoding="utf-8")
    old = time.time() - 24 * 3600
    os.utime(workspace / "_review-c178", (old, old))

    _, _, worktrees = tidy_workspace.tidy(str(workspace), today="2026-08-14")

    assert worktrees == ["_review-c178"]
    assert not (workspace / "_review-c178").exists()
    assert (workspace / "_review-c178-tidy" / "checkout.py").exists()


def test_a_worktree_git_refuses_to_remove_still_goes(workspace, monkeypatch):
    """The fallback. `git worktree remove` fails whenever the clone no longer
    knows about the worktree -- re-cloned, or made from a different checkout
    -- and a script that stopped there would leave exactly the litter it
    exists to clear. The subprocess is stubbed rather than a real repo built,
    because what is under test is what happens when it does nothing.
    """
    calls = []

    class _Refused:
        returncode = 1

    monkeypatch.setattr(tidy_workspace.subprocess, "run",
                        lambda *a, **k: (calls.append(a[0]), _Refused())[1])
    (workspace / "_review-c178").mkdir()
    old = time.time() - 24 * 3600
    os.utime(workspace / "_review-c178", (old, old))

    tidy_workspace.tidy(str(workspace), today="2026-08-14")

    assert not (workspace / "_review-c178").exists()
    assert any("remove" in call for call in calls), calls
    assert any("prune" in call for call in calls), calls
    # Every clone is offered it, because the worktree may have been made from
    # any of them -- hardcoding one orphaned the registration for the rest.
    offered = {call[2] for call in calls if "remove" in call}
    assert len(offered) == len(tidy_workspace.clones(str(workspace))), offered


def test_dry_run_changes_nothing(workspace):
    """It reports the same three lists it would act on, so the preview is the
    plan rather than a second implementation of it."""
    (workspace / "_scratch-archive-2026-08-01").mkdir()
    (workspace / "_review-c178").mkdir()
    old = time.time() - 24 * 3600
    os.utime(workspace / "_review-c178", (old, old))
    before = _names(workspace)

    archived, expired, worktrees = tidy_workspace.tidy(
        str(workspace), retention_days=7, today="2026-08-14", dry_run=True)

    assert archived == ["digest-new.md", "entry.md"]
    assert expired == ["_scratch-archive-2026-08-01"]
    assert worktrees == ["_review-c178"]
    assert _names(workspace) == before
    assert (workspace / "entry.md").exists()


def test_a_second_sweep_on_the_same_day_does_not_nest(workspace):
    """`shutil.move` into an existing directory puts the file *inside* it;
    twice in one day that builds `_scratch-archive-.../entry.md/entry.md`.
    `os.replace` overwrites, which for two drafts of one scratch file on one
    day is the answer that surprises nobody."""
    tidy_workspace.tidy(str(workspace), today="2026-08-14")
    (workspace / "entry.md").write_text("second draft", encoding="utf-8")

    tidy_workspace.tidy(str(workspace), today="2026-08-14")

    archive = workspace / "_scratch-archive-2026-08-14"
    assert (archive / "entry.md").read_text() == "second draft"
    assert (archive / "entry.md").is_file()


def test_an_empty_workspace_reports_nothing_to_tidy(tmp_path, capsys):
    assert tidy_workspace.main(["--root", str(tmp_path)]) == 0
    assert "nothing to tidy" in capsys.readouterr().out


def test_the_cli_says_what_it_did(workspace, capsys):
    (workspace / "_scratch-archive-2026-08-01").mkdir()

    tidy_workspace.main(["--root", str(workspace), "--dry-run"])

    out = capsys.readouterr().out
    assert "would archive 2 loose file(s)" in out
    assert "entry.md" in out
    assert (workspace / "entry.md").exists()


# ---- Surveying the checkouts ------------------------------------------
#
# These build real git repositories rather than stubbing `subprocess`,
# because the thing under test is what git actually answers after a squash
# merge -- a stub would answer whatever the author expected, which is the
# expectation that was wrong in the first place (Cycle 208, agora#59).

def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _git_out(cwd, *args):
    """The same call, when the test needs what git said."""
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(cwd, name, text):
    (cwd / name).write_text(text, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", "add " + name)


@pytest.fixture
def squash_merged(tmp_path):
    """A clone whose branch was squash-merged upstream after it last fetched.

    Returns `(root, repo)`. `root` holds only the clone under survey; the
    second clone that does the merging lives outside it, so `clones()` does
    not see it.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)

    merger = tmp_path / "merger"
    subprocess.run(["git", "clone", str(origin), str(merger)],
                   check=True, capture_output=True)
    _git(merger, "config", "user.email", "nova@example.com")
    _git(merger, "config", "user.name", "Nova")
    _commit(merger, "base.txt", "base\n")
    _git(merger, "push", "-q", "origin", "main")

    root = tmp_path / "workspace"
    root.mkdir()
    repo = root / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)],
                   check=True, capture_output=True)
    _git(repo, "config", "user.email", "nova@example.com")
    _git(repo, "config", "user.name", "Nova")
    _git(repo, "checkout", "-q", "-b", "nova/feature")
    _commit(repo, "feature.txt", "the work\n")
    _git(repo, "push", "-q", "origin", "nova/feature")

    # Squashed into main from the other clone, so `repo`'s own `origin/main`
    # is now behind and knows nothing about it. This is what a merged PR
    # leaves behind: the same tree under a commit the branch has never seen.
    _git(merger, "fetch", "-q", "origin")
    _git(merger, "merge", "--squash", "origin/nova/feature")
    _git(merger, "commit", "-m", "squashed (#58)")
    _git(merger, "push", "-q", "origin", "main")
    return root, repo


def test_a_stale_ref_calls_landed_work_unfinished(squash_merged):
    """The bug, stated as a test. Without the fetch the clone is comparing
    against an `origin/main` from before the merge, so the branch looks like
    two commits of work nobody has taken -- which is what sent Cycle 208 to
    open a duplicate PR."""
    root, _ = squash_merged

    survey = tidy_workspace.survey_checkouts(str(root), fetch=False)

    assert [e["verdict"] for e in survey] == ["unfinished"]


def test_the_fetch_turns_that_into_leftover(squash_merged):
    """The fix. Same clone, same branch, one fetch: the content is already on
    main, so there is nothing here for a cycle to finish."""
    root, _ = squash_merged

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["leftover"]
    assert survey[0]["branch"] == "nova/feature"
    assert survey[0]["base"] == "origin/main"


def test_work_that_really_is_unfinished_survives_the_fetch(squash_merged):
    """The other direction, and the one that stops this from being a fix that
    calls everything clean: a commit made after the squash is genuinely not on
    main, and a fetch must not launder it into `leftover`."""
    root, repo = squash_merged
    _commit(repo, "more.txt", "not merged anywhere\n")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["unfinished"]


@pytest.fixture
def pushed_past_the_merge(squash_merged):
    """The same clone, with one more commit that exists only on the remote.

    Measured on the real workspace, Cycle 384: `nova/status-word-back-on-the-card`
    was `0e94630` locally and `b1958cb` on `origin`, and that commit is on no
    other ref. A cycle pushes a follow-up and is killed, or pushes a reviewer
    fix onto a branch whose PR has already merged -- either way the work is on
    the remote and every instrument in this file reads the local HEAD.
    """
    root, repo = squash_merged
    _commit(repo, "after.txt", "only on the remote\n")
    _git(repo, "push", "-q", "origin", "nova/feature")
    _git(repo, "reset", "--hard", "-q", "HEAD~1")
    return root, repo


def test_a_commit_only_on_the_remote_is_not_litter(pushed_past_the_merge):
    """The bug, stated as a test. `leftover` reads "the branch is litter", and
    a cycle acting on that deletes a commit nothing has merged."""
    root, _ = pushed_past_the_merge

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["unfinished"]
    assert survey[0]["landed_locally"] is True
    assert len(survey[0]["remote_only"]) == 1
    assert "after.txt" in survey[0]["remote_only"][0]


def test_the_cli_says_where_the_work_actually_is(pushed_past_the_merge, capsys):
    root, _ = pushed_past_the_merge

    tidy_workspace.main(["--root", str(root)])

    out = capsys.readouterr().out
    assert "everything in this checkout has landed" in out
    assert "the work is on the remote, not here" in out
    assert "origin/nova/feature carries 1 commit(s)" in out


def test_fetching_the_remote_commit_clears_it(pushed_past_the_merge):
    """How this state actually ends, and the reason the check is reachability
    and not content: once the checkout holds the commit, `--not HEAD` excludes
    it and the sweep goes back to talking about files.

    Worth knowing the corner this leaves: a remote-only commit that is later
    *squash*-merged into main is still reported, because the squash is a new
    commit and the original is reachable from neither HEAD nor the base. That
    errs towards "look at this" rather than "delete it", which is the direction
    this whole verdict is required to err in.
    """
    root, repo = pushed_past_the_merge
    _git(repo, "merge", "-q", "--ff-only", "origin/nova/feature")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["remote_only"] == []
    assert survey[0]["landed_locally"] is False
    assert survey[0]["files"] == ["after.txt"]


def test_a_branch_level_with_its_remote_is_still_litter(squash_merged):
    """The guard against the fix firing on everything. An ordinary merged
    branch's commits are all reachable from HEAD, so the remote holds nothing
    this checkout is missing and the verdict is untouched."""
    root, _ = squash_merged

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["leftover"]
    assert survey[0]["remote_only"] == []
    assert survey[0]["landed_locally"] is False


def test_a_branch_whose_remote_ref_is_gone_is_still_litter(squash_merged):
    """Named for what it builds, which is not what it used to claim. The
    fixture pushes the branch, so this is a remote ref *deleted* after the
    merge -- ordinary post-merge cleanup -- rather than a branch never
    published. Same code path, different real scenario, and the old name
    described the one it does not set up. Reviewer finding on #337."""
    root, repo = squash_merged
    _git(repo, "push", "-q", "origin", "--delete", "nova/feature")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["leftover"]
    assert survey[0]["remote_only"] == []


def test_unfinished_names_the_files_that_differ(squash_merged):
    """"has work not on origin/main" is the same sentence for a half-built
    feature and for a `-config` clone whose only delta is a stale `image:`
    digest -- where pushing it rolls the deployment back. The file names are
    what separate the two, so the survey carries them."""
    root, repo = squash_merged
    _commit(repo, "manifest.yaml", "image: sha256:stale\n")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "unfinished"
    assert survey[0]["files"] == ["manifest.yaml"]


def test_a_clone_that_is_only_behind_carries_no_file_list(squash_merged):
    """The list answers "what is unfinished here", so a clone with nothing
    unfinished must not carry one. A clone that is merely *behind* is the case
    that makes this test capable of failing: `git diff base..HEAD` is non-empty
    there, in the other direction, so a survey that collects the names
    unconditionally would narrate main's own newer files as work somebody left.
    The `leftover` branch is not that case -- its content is identical to the
    base, so the diff is empty either way and the assertion would hold with the
    guard removed."""
    root, repo = squash_merged
    _git(repo, "checkout", "-q", "main")   # the pre-merge main, now behind

    survey = tidy_workspace.survey_checkouts(str(root))

    # Asserted after the survey, because the survey is what fetches: before it
    # runs, this clone's `origin/main` is still the stale pre-merge one and the
    # diff is empty, which would make the guard check below vacuous.
    unguarded = tidy_workspace._git(str(root), repo.name, "diff",
                                    "--name-only", "origin/main", "HEAD")
    assert unguarded.stdout.strip(), "fixture no longer exercises the guard"
    assert survey[0]["verdict"] == "clean"
    assert survey[0]["files"] == []


def test_the_cli_prints_the_differing_files(squash_merged, capsys):
    root, repo = squash_merged
    _commit(repo, "manifest.yaml", "image: sha256:stale\n")

    tidy_workspace.main(["--root", str(root)])

    out = capsys.readouterr().out
    assert "has work not on origin/main" in out
    assert "manifest.yaml" in out


def test_an_uncommitted_edit_is_named_too(squash_merged):
    """The reviewer's finding on #238, and the case that matters most: a
    `-config` clone whose stale `image:` digest was never committed reaches
    `unfinished` through `dirty` alone, where `git diff base HEAD` is empty.
    A committed-only file list is blank for exactly the clone that needs one."""
    root, repo = squash_merged
    _git(repo, "checkout", "-q", "main")
    (repo / "manifest.yaml").write_text("image: sha256:stale\n", encoding="utf-8")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "unfinished"
    assert survey[0]["ahead"] == 0          # nothing committed to diff against
    assert survey[0]["files"] == ["manifest.yaml"]


def test_a_diff_that_fails_is_not_an_empty_file_list(squash_merged,
                                                     monkeypatch):
    """`unfinished` is only reached once a difference is proven, so "no files"
    cannot legitimately happen. Reporting a failed `git diff --name-only` as an
    empty list is a failure reported as success."""
    root, repo = squash_merged
    _commit(repo, "manifest.yaml", "image: sha256:stale\n")
    real = tidy_workspace._git

    def broken(root_, clone, *args, **kwargs):
        if args[:2] == ("diff", "--name-only"):
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
        return real(root_, clone, *args, **kwargs)

    monkeypatch.setattr(tidy_workspace, "_git", broken)
    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "unfinished"
    assert survey[0]["files"] == []
    assert survey[0]["files_failed"] is True


def test_a_failed_diff_says_so_rather_than_printing_nothing(squash_merged,
                                                            monkeypatch,
                                                            capsys):
    root, repo = squash_merged
    _commit(repo, "manifest.yaml", "image: sha256:stale\n")
    real = tidy_workspace._git

    def broken(root_, clone, *args, **kwargs):
        if args[:2] == ("diff", "--name-only"):
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
        return real(root_, clone, *args, **kwargs)

    monkeypatch.setattr(tidy_workspace, "_git", broken)
    tidy_workspace.main(["--root", str(root)])

    assert "could not list which files differ" in capsys.readouterr().out


def test_uncommitted_changes_are_unfinished_even_on_main(squash_merged):
    """A cycle killed mid-edit leaves no branch and no commit at all."""
    root, repo = squash_merged
    _git(repo, "checkout", "-q", "main")
    (repo / "base.txt").write_text("half an edit\n", encoding="utf-8")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["unfinished"]
    assert survey[0]["dirty"] is True


def test_a_clone_sitting_on_an_up_to_date_main_is_clean(squash_merged):
    root, repo = squash_merged
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "origin/main")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["clean"]


def test_a_repo_with_no_origin_main_says_so_rather_than_guessing(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    repo = root / "lonely"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True,
                   capture_output=True)
    _git(repo, "config", "user.email", "nova@example.com")
    _git(repo, "config", "user.name", "Nova")
    _commit(repo, "only.txt", "no remote\n")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["no-base"]


def test_the_cli_names_the_leftover_branch(squash_merged, capsys):
    root, _ = squash_merged

    tidy_workspace.main(["--root", str(root)])

    out = capsys.readouterr().out
    assert "repo: [leftover] branch nova/feature is already on origin/main" in out
    # The tidy half found nothing, and that must not read as an all-clear for
    # the workspace as a whole.
    assert "nothing to tidy" in out


def test_a_clone_that_is_only_behind_is_not_unfinished(squash_merged):
    """Found by running this on the real workspace rather than by a test.
    Judging on content alone called three untouched clones unfinished --
    `git diff` is non-empty whichever direction the difference runs, and
    `yoyo-evolve` sits 470 commits behind with nothing local. Being behind is
    not work somebody left."""
    root, repo = squash_merged
    _git(repo, "checkout", "-q", "main")   # the pre-merge main, now behind

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["clean"]
    assert survey[0]["ahead"] == 0


def test_a_fetch_that_hangs_does_not_take_the_cycle_with_it(squash_merged,
                                                            monkeypatch,
                                                            capsys):
    """`fetch` is the only call here that leaves the box, and this runs as the
    first thing a cycle does. A hang would cost the whole hour silently, so it
    is bounded and a timeout degrades to the refs already on disk -- which is
    the stale-ref answer, said out loud rather than hidden."""
    root, _ = squash_merged
    real = subprocess.run

    def slow(cmd, **kwargs):
        if "fetch" in cmd:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return real(cmd, **kwargs)

    monkeypatch.setattr(tidy_workspace.subprocess, "run", slow)

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["unfinished"]
    assert "did not complete" in capsys.readouterr().out


def test_the_fetch_is_bounded(squash_merged, monkeypatch):
    """The timeout has to actually be passed. Asserted against the constant
    rather than against "not None", so removing the argument fails here."""
    root, _ = squash_merged
    seen = []
    real = subprocess.run

    def record(cmd, **kwargs):
        if "fetch" in cmd:
            seen.append(kwargs.get("timeout"))
        return real(cmd, **kwargs)

    monkeypatch.setattr(tidy_workspace.subprocess, "run", record)

    tidy_workspace.survey_checkouts(str(root))

    assert seen == [tidy_workspace.GIT_TIMEOUT_SECONDS]


def test_a_fetch_that_fails_is_said_out_loud(squash_merged, capsys):
    """Reviewer finding. A fetch can fail without hanging -- an unreachable
    host exits 128 in under a second -- and the survey would then answer off
    the refs on disk with no sign anything went wrong, which is the stale-ref
    bug this function exists to remove, silently reintroduced."""
    root, repo = squash_merged
    _git(repo, "remote", "set-url", "origin", "https://0.0.0.0/nope.git")

    tidy_workspace.main(["--root", str(root)])

    out = capsys.readouterr().out
    assert "repo: could not fetch" in out
    assert "may be stale" in out


def test_a_clone_that_could_not_be_fetched_says_so_even_when_clean(tmp_path,
                                                                   capsys):
    """The one a suppressed verdict would hide: `clean` drawn from a ref that
    could not be refreshed is the reassuring answer with nothing behind it."""
    root = tmp_path / "workspace"
    root.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(origin), str(seed)], check=True,
                   capture_output=True)
    _git(seed, "config", "user.email", "nova@example.com")
    _git(seed, "config", "user.name", "Nova")
    _commit(seed, "base.txt", "base\n")
    _git(seed, "push", "-q", "origin", "main")
    repo = root / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True,
                   capture_output=True)
    _git(repo, "remote", "set-url", "origin", "https://0.0.0.0/nope.git")

    tidy_workspace.main(["--root", str(root)])

    out = capsys.readouterr().out
    assert "repo: could not fetch" in out
    # And the verdict itself is still suppressed, because it is still clean.
    assert "has work not on" not in out


def test_no_fetch_does_not_claim_a_fetch_failed(squash_merged, capsys):
    """`--no-fetch` is a caller saying it has already fetched, not a failure.
    Warning there would train a cycle to ignore the warning."""
    root, _ = squash_merged

    tidy_workspace.main(["--root", str(root), "--no-fetch"])

    assert "could not fetch" not in capsys.readouterr().out


def test_a_detached_head_at_the_base_tip_is_not_called_a_leftover_branch(
        squash_merged):
    """Reviewer finding, fixed by the `ahead == 0` guard rather than by the
    branch-name comparison it replaced -- `git rev-parse --abbrev-ref HEAD`
    answers the literal string `HEAD` when detached, which never equals
    `main`, so the old logic printed "branch HEAD is litter" about a branch
    that does not exist."""
    root, repo = squash_merged
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "checkout", "-q", "--detach", "origin/main")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["branch"] == "HEAD"
    assert [e["verdict"] for e in survey] == ["clean"]


# ---- Which workspace it sweeps ----------------------------------------
#
# The bug these pin, measured Cycle 368: every concurrent cycle runs this
# tool from its own private worktree and it swept `/data/workspace` -- a
# directory that cycle does not work in -- while its own root went untouched
# and its own clones were invisible, because a linked worktree's `.git` is a
# file and `clones()` asked `isdir`. Both halves reported success.

def test_the_cycles_own_workspace_comes_first_and_the_shared_one_stays(
        tmp_path, monkeypatch):
    own = tmp_path / "concurrent" / "7"
    shared = tmp_path / "shared"
    own.mkdir(parents=True)
    shared.mkdir()
    monkeypatch.setattr(tidy_workspace, "SHARED_WORKSPACE", str(shared))

    roots = tidy_workspace.workspace_roots({"NOVA_WORKSPACE": str(own)})

    assert roots == [str(own), str(shared)]


def test_a_serialized_cycle_gets_the_shared_root_once_not_twice(
        tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(tidy_workspace, "SHARED_WORKSPACE", str(shared))

    roots = tidy_workspace.workspace_roots({"NOVA_WORKSPACE": str(shared)})

    assert roots == [str(shared)]


def test_a_workspace_variable_pointing_nowhere_is_dropped(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(tidy_workspace, "SHARED_WORKSPACE", str(shared))

    roots = tidy_workspace.workspace_roots(
        {"NOVA_WORKSPACE": str(tmp_path / "never-created")})

    assert roots == [str(shared)]


def test_an_unset_variable_still_gives_the_shared_root(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(tidy_workspace, "SHARED_WORKSPACE", str(shared))

    assert tidy_workspace.workspace_roots({}) == [str(shared)]


def test_a_linked_worktree_is_a_clone_even_though_its_git_is_a_file(tmp_path):
    """`git worktree add` writes `.git` as a one-line file, not a directory.

    Built with real git rather than by writing a `.git` file by hand: the
    thing under test is what git actually lays down for a worktree, which is
    exactly the assumption `isdir` got wrong.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "n@example.com")
    _git(origin, "config", "user.name", "Nova")
    _commit(origin, "a.txt", "one")

    root = tmp_path / "concurrent"
    root.mkdir()
    _git(origin, "worktree", "add", "--detach", str(root / "origin"), "HEAD")

    assert (root / "origin" / ".git").is_file()
    assert tidy_workspace.clones(str(root)) == ["origin"]


def test_the_survey_sees_a_linked_worktree(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "n@example.com")
    _git(origin, "config", "user.name", "Nova")
    _commit(origin, "a.txt", "one")

    root = tmp_path / "concurrent"
    root.mkdir()
    _git(origin, "worktree", "add", "--detach", str(root / "origin"), "HEAD")

    surveyed = tidy_workspace.survey_checkouts(str(root), fetch=False)

    assert [entry["clone"] for entry in surveyed] == ["origin"]


def test_both_roots_are_swept_and_each_is_named(tmp_path, monkeypatch, capsys):
    own = tmp_path / "own"
    shared = tmp_path / "shared"
    own.mkdir()
    shared.mkdir()
    (own / "entry.md").write_text("draft", encoding="utf-8")
    (shared / "digest-new.md").write_text("draft", encoding="utf-8")
    monkeypatch.setattr(tidy_workspace, "SHARED_WORKSPACE", str(shared))
    monkeypatch.setenv("NOVA_WORKSPACE", str(own))

    assert tidy_workspace.main([]) == 0

    out = capsys.readouterr().out
    assert "== %s (yours)" % (own,) in out
    assert "== %s (shared)" % (shared,) in out
    assert "entry.md" in out and "digest-new.md" in out
    stamp = "_scratch-archive-" + tidy_workspace._today()
    assert (own / stamp / "entry.md").exists()
    assert (shared / stamp / "digest-new.md").exists()


def test_one_root_prints_no_heading(workspace, capsys):
    """The single-root output every existing caller reads is unchanged."""
    tidy_workspace.main(["--root", str(workspace), "--dry-run"])

    assert "== " not in capsys.readouterr().out


def test_the_survey_reports_the_root_it_was_given_not_the_shared_one(
        squash_merged, monkeypatch, capsys):
    """The failure in one line: a verdict about a directory you do not work in."""
    root, _repo = squash_merged
    shared = os.path.join(root, "..", "not-swept")
    monkeypatch.setattr(tidy_workspace, "SHARED_WORKSPACE", shared)
    monkeypatch.setenv("NOVA_WORKSPACE", str(root))

    tidy_workspace.main(["--no-fetch"])

    out = capsys.readouterr().out
    assert "repo: [unfinished] branch" in out
    assert "not-swept" not in out


def test_a_reviewer_worktree_is_not_surveyed_as_a_clone(tmp_path):
    """`_review-*` is a linked worktree too, and widening `.git` matched it.

    Reproduced by the reviewer on runner#319: `clones()` answered
    `['_review-c178', 'repo']`, the survey printed a verdict for a checkout a
    reader was mid-read in, and `_remove_worktree` fed it back in as a
    candidate owner and had it deregister itself. The old `isdir` predicate
    excluded these by accident; this pins it on purpose.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "n@example.com")
    _git(origin, "config", "user.name", "Nova")
    _commit(origin, "a.txt", "one")

    root = tmp_path / "root"
    root.mkdir()
    _git(origin, "worktree", "add", "--detach", str(root / "repo"), "HEAD")
    _git(origin, "worktree", "add", "--detach", str(root / "_review-c368"), "HEAD")

    # The precondition, asserted rather than assumed: without it the negative
    # below is guaranteed whether or not the exclusion exists.
    assert (root / "_review-c368" / ".git").is_file()

    assert tidy_workspace.clones(str(root)) == ["repo"]
    assert [e["clone"] for e in
            tidy_workspace.survey_checkouts(str(root), fetch=False)] == ["repo"]


def test_the_owner_search_is_never_offered_a_review_worktree(tmp_path, monkeypatch):
    """`_remove_worktree` takes its candidate owners from `clones()`."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "n@example.com")
    _git(origin, "config", "user.name", "Nova")
    _commit(origin, "a.txt", "one")

    root = tmp_path / "root"
    root.mkdir()
    _git(origin, "worktree", "add", "--detach", str(root / "repo"), "HEAD")
    review = root / "_review-c368"
    _git(origin, "worktree", "add", "--detach", str(review), "HEAD")
    os.utime(review, (0, 0))

    offered = []
    real = tidy_workspace._remove_worktree

    def spy(root_, name, clone_names):
        offered.extend(clone_names)
        return real(root_, name, clone_names)

    monkeypatch.setattr(tidy_workspace, "_remove_worktree", spy)
    _archived, _expired, worktrees = tidy_workspace.tidy(
        str(root), today="2026-08-24")

    assert worktrees == ["_review-c368"]
    assert offered == ["repo"]


def test_the_default_root_is_read_when_called_not_when_imported(tmp_path, monkeypatch):
    """`NOVA_WORKSPACE` set after import must still be the root that is swept."""
    own = tmp_path / "own"
    own.mkdir()
    (own / "entry.md").write_text("draft", encoding="utf-8")
    monkeypatch.setattr(tidy_workspace, "SHARED_WORKSPACE", str(tmp_path / "gone"))
    monkeypatch.setenv("NOVA_WORKSPACE", str(own))

    archived, _expired, _worktrees = tidy_workspace.tidy(today="2026-08-24")

    assert archived == ["entry.md"]
    assert (own / "_scratch-archive-2026-08-24" / "entry.md").exists()


# --- a base that moves, not just a ref that is stale -------------------------
#
# `test_the_fetch_turns_that_into_leftover` above holds only while main stops
# at the squash. When main carries on -- six PRs in the four hours after #316,
# on this loop's real cadence -- `git diff origin/main HEAD` fills with main's
# own newer files and the branch reads `unfinished` again. Every test in this
# file agreed the fetch had fixed it, and the shared checkout was being
# misreported the whole time.


@pytest.fixture(autouse=True)
def no_real_gh(monkeypatch):
    """Nothing in this file may reach GitHub.

    The survey now asks `gh` whether a branch was merged. In a tmp_path clone
    of a bare local origin that question has no answer, and a test that waited
    for `gh` to work it out would be a network call in a unit suite. The
    default is "could not ask", which is the same verdict these tests asserted
    before the check existed; the tests below override it deliberately.
    """
    monkeypatch.setattr(tidy_workspace, "_merged_pr", lambda *a, **k: (None, False))


@pytest.fixture
def moved_on(squash_merged):
    """The squash-merged clone, plus a later commit on main touching nothing
    the branch touched. Content alone can no longer tell landed from
    unfinished here -- only GitHub can."""
    root, repo = squash_merged
    merger = root.parent / "merger"
    _commit(merger, "later.txt", "main moved on\n")
    _git(merger, "push", "-q", "origin", "main")
    return root, repo


def test_a_moving_base_calls_landed_work_unfinished_without_the_pr_check(moved_on):
    """The bug as measured on the real workspace: fetched, up to date, and
    still `unfinished`, because main has files the branch has never seen."""
    root, _ = moved_on

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["unfinished"]


def test_a_merged_pr_makes_it_leftover(moved_on, monkeypatch):
    """The fix. GitHub says a PR merged from this head and the branch has
    nothing on top of it, so the branch is litter however far main has run."""
    root, _ = moved_on
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316,
                                          "mergedAt": "2099-01-01T00:00:00Z"}, True))

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["leftover"]
    assert survey[0]["merged_pr"] == 316


def test_a_commit_on_top_of_a_merged_pr_is_still_unfinished(moved_on, monkeypatch):
    """The direction that stops this laundering real work. A merged PR is not
    a promise that nothing was written after it."""
    root, _ = moved_on
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316,
                                          "mergedAt": "2000-01-01T00:00:00Z"}, True))

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["unfinished"]
    assert survey[0]["commits_after_merge"] is True


def test_uncommitted_work_never_asks_github(moved_on, monkeypatch):
    """A merged PR says nothing about an edit a cycle was killed halfway
    through, so the question is not even asked."""
    root, repo = moved_on
    (repo / "half-written.txt").write_text("mid-edit", encoding="utf-8")
    asked = []
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: (asked.append(a) or
                                         ({"number": 316,
                                           "mergedAt": "2099-01-01T00:00:00Z"}, True)))

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["unfinished"]
    assert asked == []


def test_an_unanswerable_gh_says_so_rather_than_reading_as_unfinished(moved_on, capsys):
    """`gh` absent and `gh` answering "no merged PR" mean opposite things. The
    first must not be printed as the second in silence."""
    root, _ = moved_on

    for entry in tidy_workspace.survey_checkouts(str(root)):
        assert entry["merged_pr_checked"] is False
    tidy_workspace.main(["--root", str(root), "--no-fetch"])

    out = capsys.readouterr().out
    assert "could not ask GitHub whether this branch was merged" in out


def test_a_gh_that_answers_no_merged_pr_prints_no_caveat(moved_on, monkeypatch, capsys):
    """The other half of the same distinction: asked and answered "none" is
    a real `unfinished`, and adding a caveat to it would train cycles to
    ignore the caveat."""
    root, _ = moved_on
    monkeypatch.setattr(tidy_workspace, "_merged_pr", lambda *a, **k: (None, True))

    tidy_workspace.main(["--root", str(root), "--no-fetch"])

    out = capsys.readouterr().out
    assert "could not ask GitHub" not in out
    assert "[unfinished]" in out


def test_the_verdict_word_is_printed_beside_the_sentence(moved_on, monkeypatch, capsys):
    """`prompt.md` step 1c describes this output by the verdict words. Two
    cycles looked for them, found prose, and filed the feature as missing."""
    root, _ = moved_on
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316,
                                          "mergedAt": "2099-01-01T00:00:00Z"}, True))

    tidy_workspace.main(["--root", str(root), "--no-fetch"])

    out = capsys.readouterr().out
    assert "[leftover]" in out
    assert "already merged as #316" in out
    assert "the branch is litter" in out


# --- the gh call itself ------------------------------------------------------
#
# Everything above stubs `_merged_pr`, so these are the only tests that see it.
# The distinction it exists to preserve -- "asked, no merged PR" versus "could
# not ask" -- is decided entirely in here.


# The autouse fixture above stubs `_merged_pr` for every test in this file, so
# the real one is held here, taken at import before any stub exists.
_REAL_MERGED_PR = tidy_workspace._merged_pr


def _gh_returning(monkeypatch, **result):
    """Replace the `gh` subprocess and nothing else."""
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd and cmd[0] == "gh":
            if "raises" in result:
                raise result["raises"]
            return types.SimpleNamespace(returncode=result.get("returncode", 0),
                                         stdout=result.get("stdout", ""),
                                         stderr="")
        return real(cmd, *a, **k)

    monkeypatch.setattr(tidy_workspace.subprocess, "run", fake)


def test_merged_pr_reads_the_row_gh_prints(tmp_path, monkeypatch):
    _gh_returning(monkeypatch,
                  stdout='[{"number": 316, "mergedAt": "2026-08-24T10:57:11Z"}]')

    pr, checked = _REAL_MERGED_PR(str(tmp_path), "repo", "nova/x")

    assert checked is True
    assert pr["number"] == 316


def test_merged_pr_distinguishes_none_from_could_not_ask(tmp_path, monkeypatch):
    """An empty list is an answer. A non-zero exit is not."""
    _gh_returning(monkeypatch, stdout="[]")
    assert _REAL_MERGED_PR(str(tmp_path), "repo", "nova/x") == (None, True)

    _gh_returning(monkeypatch, returncode=1, stdout="")
    assert _REAL_MERGED_PR(str(tmp_path), "repo", "nova/x") == (None, False)


def test_merged_pr_survives_gh_missing_or_hanging(tmp_path, monkeypatch):
    """`gh` is not guaranteed to exist wherever this runs, and it talks to the
    network. Neither may take the sweep down -- this is the first thing a
    cycle runs."""
    _gh_returning(monkeypatch, raises=FileNotFoundError("gh"))
    assert _REAL_MERGED_PR(str(tmp_path), "repo", "nova/x") == (None, False)

    _gh_returning(monkeypatch,
                  raises=subprocess.TimeoutExpired(cmd="gh", timeout=30))
    assert _REAL_MERGED_PR(str(tmp_path), "repo", "nova/x") == (None, False)

    _gh_returning(monkeypatch, stdout="not json")
    assert _REAL_MERGED_PR(str(tmp_path), "repo", "nova/x") == (None, False)


def test_merged_pr_does_not_ask_about_a_base_branch(tmp_path, monkeypatch):
    """`main` is not a feature branch, and a detached HEAD has no branch name
    to ask about. Neither is a question worth a network call."""
    def explode(*a, **k):
        raise AssertionError("gh must not run")

    monkeypatch.setattr(tidy_workspace.subprocess, "run", explode)

    for branch in ("main", "master", "HEAD", ""):
        assert _REAL_MERGED_PR(str(tmp_path), "repo", branch) == (None, False)


def test_an_unparseable_merge_stamp_leaves_the_branch_unfinished(tmp_path):
    """`_committed_after` answers True on anything it cannot read, because the
    safe error is to leave real work looking like real work."""
    assert tidy_workspace._committed_after(str(tmp_path), "repo", None) is True
    assert tidy_workspace._committed_after(str(tmp_path), "repo", "not a date") is True


# --- deciding litter from the graph, not from two clocks ---------------------
#
# The first version of this compared the branch tip's committer date to the
# PR's mergedAt. A reviewer called that a blocker and was right: it is wrong in
# the direction that loses work. `git rebase --committer-date-is-author-date`,
# an unsynced container clock and an explicit `GIT_COMMITTER_DATE` all give a
# tip that timestamps before a merge it does not contain.


def test_the_merged_head_itself_is_landed(squash_merged):
    """HEAD is exactly what GitHub merged. No clock consulted."""
    root, repo = squash_merged
    head = _git_out(repo, "rev-parse", "HEAD")

    assert tidy_workspace._tip_contains_merge(str(root), "repo", head) is True


def test_a_commit_on_top_of_the_merged_head_is_not_landed(squash_merged):
    """The graph says it directly: HEAD descends from the merged commit, so
    there is real work sitting on top of it."""
    root, repo = squash_merged
    head = _git_out(repo, "rev-parse", "HEAD")
    _commit(repo, "after.txt", "written after the merge\n")

    assert tidy_workspace._tip_contains_merge(str(root), "repo", head) is False


def test_an_oid_this_clone_has_never_seen_is_not_an_answer(squash_merged):
    """The ordinary case: the remote branch was deleted after its merge, so
    the merged commit is in no local object store. `None` means "ask something
    else", not "landed"."""
    root, _ = squash_merged

    assert tidy_workspace._tip_contains_merge(
        str(root), "repo", "0" * 40) is None
    assert tidy_workspace._tip_contains_merge(str(root), "repo", "") is None


def test_a_clock_behind_the_merge_cannot_launder_real_work(moved_on, monkeypatch):
    """The reviewer's scenario, as a test. Real commits on top of the merged
    head, and a tip whose committer date is set *before* the merge -- which a
    rebase flag or an unsynced clock produces without any malice. The graph
    answers first, so the clock never gets to say `leftover`."""
    root, repo = moved_on
    head = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "-c", "user.email=n@example.com", "-c", "user.name=Nova",
         "commit", "--allow-empty", "-m", "real work, old timestamp")
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316, "headRefOid": head,
                                          "mergedAt": "2099-01-01T00:00:00Z"}, True))

    survey = tidy_workspace.survey_checkouts(str(root))

    assert [e["verdict"] for e in survey] == ["unfinished"]
    assert survey[0]["commits_after_merge"] is True


def test_a_verdict_that_rests_on_the_clock_says_so(moved_on, monkeypatch, capsys):
    """When the merged commit is not in the clone there is nothing but the
    timestamps, and that has to be visible before anyone deletes a branch."""
    root, _ = moved_on
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316, "headRefOid": "0" * 40,
                                          "mergedAt": "2099-01-01T00:00:00Z"}, True))

    survey = tidy_workspace.survey_checkouts(str(root))
    assert survey[0]["rests_on_clock"] is True
    tidy_workspace.main(["--root", str(root), "--no-fetch"])

    assert "rests on the commit date" in capsys.readouterr().out


def test_a_graph_answer_does_not_say_it_rests_on_the_clock(squash_merged, monkeypatch):
    root, repo = squash_merged
    head = _git_out(repo, "rev-parse", "HEAD")
    _commit(repo, "later-on-main.txt", "x\n")
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316, "headRefOid": head,
                                          "mergedAt": "1999-01-01T00:00:00Z"}, True))

    survey = tidy_workspace.survey_checkouts(str(root), fetch=False)

    assert survey[0]["rests_on_clock"] is False


def test_one_unanswerable_gh_stops_the_sweep_asking_again(moved_on, monkeypatch):
    """A systemic failure fails identically for every clone, and each attempt
    costs up to GH_TIMEOUT_SECONDS on the first thing a cycle runs."""
    root, repo = moved_on
    for extra in ("second", "third"):
        subprocess.run(["git", "clone", str(repo), str(root / extra)],
                       check=True, capture_output=True)
        # CI has no global git identity; a clone inherits none either.
        _git(root / extra, "config", "user.email", "nova@example.com")
        _git(root / extra, "config", "user.name", "Nova")
        _git(root / extra, "checkout", "-q", "-b", "nova/" + extra)
        _commit(root / extra, extra + ".txt", "work\n")
    calls = []
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: (calls.append(a[2]), (None, False))[1])

    tidy_workspace.survey_checkouts(str(root), fetch=False)

    assert len(calls) == 1


def test_no_gh_skips_the_question_entirely(moved_on, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("gh must not be asked")

    root, _ = moved_on
    monkeypatch.setattr(tidy_workspace, "_merged_pr", explode)

    survey = tidy_workspace.survey_checkouts(str(root), fetch=False,
                                             ask_github=False)

    assert [e["verdict"] for e in survey] == ["unfinished"]


def test_merged_pr_takes_the_newest_of_a_reused_branch_name(tmp_path, monkeypatch):
    """`--limit 1` trusted an ordering gh does not document, and this loop
    reuses `nova/<slug>` names readily."""
    _gh_returning(monkeypatch, stdout=json.dumps([
        {"number": 100, "mergedAt": "2026-01-01T00:00:00Z", "headRefOid": "a"},
        {"number": 316, "mergedAt": "2026-08-24T10:57:11Z", "headRefOid": "b"},
        {"number": 200, "mergedAt": "2026-05-01T00:00:00Z", "headRefOid": "c"},
    ]))

    pr, checked = _REAL_MERGED_PR(str(tmp_path), "repo", "nova/x")

    assert checked is True
    assert pr["number"] == 316


def test_a_demoted_branch_carries_no_file_list_of_mains_own_work(
        moved_on, monkeypatch):
    """The half that is easy to get wrong, and the one measured live.

    Everything in this checkout landed, so `git diff origin/main HEAD` here
    lists what *main* has done since -- 120 files of it on the real workspace,
    Cycle 384, none of them the outstanding commit. That is the Cycle 372 bug
    arriving through a new door: a file list that narrates main's own work as
    work somebody abandoned. What is outstanding is on the remote, and the
    line naming it is the only one this entry should print.
    """
    root, repo = moved_on
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316,
                                          "mergedAt": "2099-01-01T00:00:00Z"},
                                         True))
    _commit(repo, "after.txt", "only on the remote\n")
    _git(repo, "push", "-q", "origin", "nova/feature")
    _git(repo, "reset", "--hard", "-q", "HEAD~1")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "unfinished"
    assert survey[0]["landed_locally"] is True
    assert survey[0]["files"] == []
    assert "after.txt" in survey[0]["remote_only"][0]


def test_a_checkout_behind_its_own_merged_head_is_still_litter(
        moved_on, monkeypatch):
    """The false positive that nearly shipped, stated as a test.

    Measured Cycle 384: the shared checkout sat at `0e94630` while `origin/` the
    same branch *and* PR #316's own `headRefOid` were both `b1958cb`. Nothing
    was outstanding -- one clone was one commit stale -- and the first version
    of this fix called that "the work is on the remote, not here". A sweep that
    reports every checkout which has not caught up to its own merged branch is
    noise, and noise on the first tool of every cycle is what stops being read.
    """
    root, repo = moved_on
    _commit(repo, "after.txt", "part of the PR, merged with it\n")
    _git(repo, "push", "-q", "origin", "nova/feature")
    merged_head = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "--hard", "-q", "HEAD~1")
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316,
                                          "headRefOid": merged_head,
                                          "mergedAt": "2099-01-01T00:00:00Z"},
                                         True))

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["remote_only"] == []
    assert survey[0]["verdict"] == "leftover"


def test_a_commit_pushed_past_the_merged_head_is_still_reported(
        moved_on, monkeypatch):
    """The other direction, so the exclusion above cannot launder real work.

    Same shape, one commit further: GitHub's merged head is the older commit
    and something was pushed on top of it afterwards. That is outstanding.
    """
    root, repo = moved_on
    _commit(repo, "after.txt", "part of the PR\n")
    merged_head = _git_out(repo, "rev-parse", "HEAD")
    _commit(repo, "later.txt", "pushed after the merge\n")
    _git(repo, "push", "-q", "origin", "nova/feature")
    _git(repo, "reset", "--hard", "-q", "HEAD~2")
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316,
                                          "headRefOid": merged_head,
                                          "mergedAt": "2099-01-01T00:00:00Z"},
                                         True))

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "unfinished"
    assert [c.split(" ", 1)[1] for c in survey[0]["remote_only"]] \
        == ["add later.txt"]


def test_a_git_log_that_fails_is_not_an_empty_remote(pushed_past_the_merge,
                                                     monkeypatch):
    """A failure reported as success, in the one verdict that deletes things.

    Reviewer finding on #337, and it reproduced: the first version returned a
    bare `None` for "could not ask" and the caller's `or []` read that as
    "nothing outstanding", handing back `leftover` -- "the branch is litter" --
    over a real remote-only commit. A concurrent `fetch --prune` on the shared
    clone is enough to cause it, and this loop runs three cycles at once.
    """
    real = tidy_workspace._git

    def broken(root_, clone, *args, **kwargs):
        if args and args[0] == "log" and "--not" in args:
            return types.SimpleNamespace(returncode=128, stdout="", stderr="")
        return real(root_, clone, *args, **kwargs)

    monkeypatch.setattr(tidy_workspace, "_git", broken)
    root, _ = pushed_past_the_merge

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["remote_only_failed"] is True
    assert survey[0]["remote_only"] == []


def test_a_failed_remote_check_says_so_under_leftover(pushed_past_the_merge,
                                                      monkeypatch, capsys):
    """And it has to be said where it matters. The verdict deliberately stands
    -- flipping it on any transient git failure would have the sweep crying
    wolf on lock contention -- so the caveat is the whole of the protection,
    and it must print under `leftover`, not only under `unfinished`."""
    real = tidy_workspace._git

    def broken(root_, clone, *args, **kwargs):
        if args and args[0] == "log" and "--not" in args:
            return types.SimpleNamespace(returncode=128, stdout="", stderr="")
        return real(root_, clone, *args, **kwargs)

    monkeypatch.setattr(tidy_workspace, "_git", broken)
    root, _ = pushed_past_the_merge

    tidy_workspace.main(["--root", str(root)])

    out = capsys.readouterr().out
    assert "the branch is litter" in out
    assert "could not list what origin/nova/feature carries" in out


def test_no_remote_ref_is_not_reported_as_a_failure(squash_merged):
    """The other half of the same distinction, and the reason it is two values
    rather than one. A branch with no `origin/` ref has nothing to report and
    nothing went wrong, so a caveat there would be noise on every clone whose
    branch was cleaned up after its merge."""
    root, repo = squash_merged
    _git(repo, "push", "-q", "origin", "--delete", "nova/feature")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["remote_only_failed"] is False
    assert survey[0]["remote_only"] == []


def test_a_remote_only_commit_squash_merged_into_main_is_still_reported(
        pushed_past_the_merge):
    """The corner #337's PR body claimed was "written into a test" when it was
    only written into a comment. Reviewer finding, and it is fair: a claim
    about behaviour belongs where it can fail.

    A squash creates a new commit, so the original stays reachable from neither
    HEAD nor the base and is still listed. That errs towards "come and look"
    rather than "delete it", which is the only direction this verdict may err.
    """
    root, repo = pushed_past_the_merge
    merger = root.parent / "merger"
    _git(merger, "fetch", "-q", "origin")
    _git(merger, "merge", "--squash", "origin/nova/feature")
    _git(merger, "commit", "-m", "squashed the follow-up too (#317)")
    _git(merger, "push", "-q", "origin", "main")

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["remote_only_failed"] is False
    assert [c.split(" ", 1)[1] for c in survey[0]["remote_only"]] \
        == ["add after.txt"]


def test_being_behind_the_merged_head_is_answered_by_the_graph(squash_merged):
    """The commits already know, and until Cycle 386 two clocks were asked.

    A checkout that is an *ancestor* of the commit GitHub merged has nothing
    sitting on top of that merge, by construction. `_tip_contains_merge` had
    an answer for the tip itself and for a descendant of it, and fell through
    to `_committed_after` for this one -- which is the ordinary shape of a
    clone one commit stale, not a corner.
    """
    root, repo = squash_merged
    _commit(repo, "after.txt", "part of the PR, merged with it\n")
    merged_head = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "--hard", "-q", "HEAD~1")

    assert tidy_workspace._tip_contains_merge(
        str(root), "repo", merged_head) is True


def test_a_checkout_behind_its_merged_head_no_longer_rests_on_the_clock(
        moved_on, monkeypatch):
    """Same verdict as before this change, reached without a wall clock.

    The `mergedAt` here is deliberately in the past, which is the reading that
    would have said "committed after the merge" and called the branch
    unfinished. The graph overrules it, so the timestamp is never consulted.
    """
    root, repo = moved_on
    _commit(repo, "after.txt", "part of the PR, merged with it\n")
    merged_head = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "push", "-q", "origin", "nova/feature")
    _git(repo, "reset", "--hard", "-q", "HEAD~1")
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316,
                                          "headRefOid": merged_head,
                                          "mergedAt": "1999-01-01T00:00:00Z"},
                                         True))

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "leftover"
    assert survey[0]["rests_on_clock"] is False


# --- a clean checkout is asked about its remote too --------------------------


@pytest.fixture
def clean_but_pushed(squash_merged):
    """A clone reading `clean` while `origin/<branch>` carries a real commit.

    The checkout has been reset onto the fetched base, which is what a cycle
    that pulled after its own merge leaves behind: nothing here that the base
    has not got, so every local instrument says there is nothing to finish.
    The branch's own commit is on the remote and on no other ref.
    """
    root, repo = squash_merged
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "reset", "--hard", "-q", "origin/main")
    return root, repo


def test_a_clean_checkout_is_asked_about_its_remote(clean_but_pushed):
    """The gap, stated as a test. `remote_only` ran for `leftover` and
    `unfinished` only, and `clean` is the one verdict the sweep prints nothing
    for -- so this commit was invisible in exactly the way the remote check
    exists to stop."""
    root, _ = clean_but_pushed

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "unfinished"
    assert survey[0]["landed_locally"] is True
    assert [c.split(" ", 1)[1] for c in survey[0]["remote_only"]] \
        == ["add feature.txt"]


def test_a_clean_checkout_behind_its_merged_head_stays_clean(clean_but_pushed,
                                                             monkeypatch):
    """The direction that stops this becoming noise on every clone.

    A squash leaves the branch's original commits reachable from neither HEAD
    nor main, so a merged branch whose remote ref was never deleted lists them
    here and none of them are outstanding. The second pass excludes the head
    GitHub says it merged, and the answer goes back to `clean`.
    """
    root, repo = clean_but_pushed
    merged_head = _git_out(repo, "rev-parse", "origin/nova/feature")
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: ({"number": 316,
                                          "headRefOid": merged_head,
                                          "mergedAt": "2099-01-01T00:00:00Z"},
                                         True))

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "clean"
    assert survey[0]["remote_only"] == []


def test_a_clean_checkout_on_the_base_branch_asks_nothing(squash_merged,
                                                          monkeypatch):
    """The cheap path stays cheap: the common clone is parked on `main`, and
    it must reach neither `git log` for a remote ref that is the base nor
    GitHub."""
    root, repo = squash_merged
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "reset", "--hard", "-q", "origin/main")
    asked = []
    monkeypatch.setattr(tidy_workspace, "_merged_pr",
                        lambda *a, **k: (asked.append(a) or (None, False)))
    # Asserted on `git log` rather than only on the verdict, because the
    # verdict is `clean` whether or not this path exists -- the reviewer
    # caught the first version of this test agreeing with the author either
    # way. `_remote_only_commits` returns before it spawns anything for a
    # base branch, and that early return is the thing under test.
    real = tidy_workspace._git
    logged = []

    def watched(root_, clone, *args, **kwargs):
        if args and args[0] == "log" and "--not" in args:
            logged.append(args)
        return real(root_, clone, *args, **kwargs)

    monkeypatch.setattr(tidy_workspace, "_git", watched)

    survey = tidy_workspace.survey_checkouts(str(root))

    assert survey[0]["verdict"] == "clean"
    assert survey[0]["remote_only"] == []
    assert asked == []
    assert logged == []


def test_a_clean_checkout_whose_remote_check_failed_says_so(clean_but_pushed,
                                                            monkeypatch,
                                                            capsys):
    """`clean` is silent by design, and silence is the wrong answer when the
    one check that could have contradicted it never ran. Same rule this file
    already applies to `leftover`: a failure reported as success is the shape
    that costs more than having no check."""
    real = tidy_workspace._git

    def broken(root_, clone, *args, **kwargs):
        if args and args[0] == "log" and "--not" in args:
            return types.SimpleNamespace(returncode=128, stdout="", stderr="")
        return real(root_, clone, *args, **kwargs)

    monkeypatch.setattr(tidy_workspace, "_git", broken)
    root, _ = clean_but_pushed

    survey = tidy_workspace.survey_checkouts(str(root))
    assert survey[0]["verdict"] == "clean"
    assert survey[0]["remote_only_failed"] is True

    tidy_workspace.main(["--root", str(root), "--no-fetch"])
    out = capsys.readouterr().out
    assert "could not list what origin/nova/feature carries" in out


# --- the remote sweep: branches no checkout is parked on -------------------
#
# Everything above this line asks about a directory on this disk. These ask
# GitHub, so the fake is at `subprocess.run` rather than at `_git`: that keeps
# `_gh_lines` and `_gh_json` -- including the "a failure is not an empty
# answer" split that the rest of this file exists to defend -- inside the test
# rather than mocked past.


def _gh_fake(branches, merged=(), opened=(), compares=None, broken=()):
    """A `subprocess.run` that answers the four `gh` calls the sweep makes."""
    compares = compares or {}

    def run(argv, **kwargs):
        joined = " ".join(argv)
        for mark in broken:
            if mark in joined:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if "/branches" in joined:
            return types.SimpleNamespace(
                returncode=0, stderr="",
                stdout="".join("%s\t%s\n" % pair for pair in branches))
        if "compare/" in joined:
            branch = joined.split("...", 1)[1].split(" ", 1)[0]
            found = compares.get(branch)
            if found is None:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="")
            return types.SimpleNamespace(returncode=0, stderr="",
                                         stdout=json.dumps(found))
        if "--state merged" in joined:
            return types.SimpleNamespace(returncode=0, stderr="",
                                         stdout=json.dumps(list(merged)))
        if "--state open" in joined:
            return types.SimpleNamespace(returncode=0, stderr="",
                                         stdout=json.dumps(list(opened)))
        if joined.startswith("gh api repos/"):
            return types.SimpleNamespace(returncode=0, stdout="main\n", stderr="")
        raise AssertionError("unexpected gh call: %s" % joined)

    return run


def _compare(ahead, files=("a.py",), date="2026-08-24T13:59:44Z"):
    return {"ahead": ahead, "files": list(files), "date": date}


def test_a_pushed_branch_with_no_pr_is_reported(monkeypatch):
    """The case this whole sweep exists for: a cycle pushed and then died.

    Nothing else in this file can see it. No checkout is parked on the branch,
    so `survey_checkouts` never names it, and there is no PR for `gh pr list`
    to return."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("main", "aaa"), ("nova/killed-midway", "bbb")],
        compares={"nova/killed-midway": _compare(3, ["tools/x.py"])}))

    survey = tidy_workspace.survey_remote_branches(["o/r"])

    assert survey[0]["checked"] is True
    assert [row["branch"] for row in survey[0]["outstanding"]] == \
        ["nova/killed-midway"]
    row = survey[0]["outstanding"][0]
    assert row["kind"] == "no-pr" and row["ahead"] == 3
    assert row["date"] == "2026-08-24" and row["files"] == ["tools/x.py"]


def test_a_branch_that_moved_past_its_merged_head_is_reported(monkeypatch):
    """The merged part landed; whatever was pushed on top of it did not."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/feature", "newer")],
        merged=[{"number": 316, "headRefName": "nova/feature",
                 "headRefOid": "older", "mergedAt": "2026-08-24T10:46:35Z"}],
        compares={"nova/feature": _compare(1)}))

    row = tidy_workspace.survey_remote_branches(["o/r"])[0]["outstanding"][0]

    assert row["kind"] == "past-merged-head" and row["merged_pr"] == 316


def test_the_branch_a_merge_left_behind_is_not_reported(monkeypatch):
    """270 of the runner's 290 branches are this. Reporting them is noise."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/landed", "same")],
        merged=[{"number": 300, "headRefName": "nova/landed",
                 "headRefOid": "same", "mergedAt": "2026-08-24T10:00:00Z"}]))

    assert tidy_workspace.survey_remote_branches(["o/r"])[0]["outstanding"] == []


def test_a_branch_with_an_open_pr_is_not_reported(monkeypatch):
    """Work in flight is being tracked by the thing that tracks work."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/in-flight", "bbb")],
        opened=[{"number": 341, "headRefName": "nova/in-flight"}],
        compares={"nova/in-flight": _compare(2)}))

    assert tidy_workspace.survey_remote_branches(["o/r"])[0]["outstanding"] == []


def test_a_branch_the_base_already_contains_is_not_reported(monkeypatch):
    """`ahead == 0` is litter however it got that way -- a rebase, a rewrite,
    a merge whose head oid no longer matches. The question is whether there is
    work on it, and there is not."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/rewritten", "bbb")],
        compares={"nova/rewritten": _compare(0, [])}))

    assert tidy_workspace.survey_remote_branches(["o/r"])[0]["outstanding"] == []


def test_branches_outside_the_prefixes_are_counted_not_dropped(monkeypatch):
    """`platform-config` carries 42 branches other automation wrote. They are
    out of scope, and the number of them is printed rather than swallowed --
    the same rule this repo applies to every other cap it takes."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/mine", "bbb"), ("deploy/prod/theirs", "ccc"),
                  ("sealed-secrets/20260310", "ddd")],
        compares={"nova/mine": _compare(1)}))

    entry = tidy_workspace.survey_remote_branches(["o/r"])[0]

    assert entry["mine"] == 1 and entry["skipped_prefix"] == 2


def test_gh_failing_is_not_an_empty_answer(monkeypatch):
    """The rule the rest of this file is built on. `checked: False` and an
    empty `outstanding` read identically to a caller that only looks at the
    list, so the caller is told which one it got."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/mine", "bbb")], broken=["/branches"]))

    entry = tidy_workspace.survey_remote_branches(["o/r"])[0]

    assert entry["checked"] is False and entry["outstanding"] == []


def test_a_failed_compare_is_reported_rather_than_dropped(monkeypatch):
    """A compare that could not run must not silently remove the branch it
    could not judge -- that would hide exactly the branch this sweep exists
    to surface, and hide it with no line saying so."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/unknown", "bbb")], compares={}))

    row = tidy_workspace.survey_remote_branches(["o/r"])[0]["outstanding"][0]

    assert row["compared"] is False and row["ahead"] is None


def test_the_newest_merge_decides_a_reused_branch_name(monkeypatch):
    """`nova/<slug>` collides readily and `_merged_pr` already learned this."""
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/reused", "second")],
        merged=[{"number": 10, "headRefName": "nova/reused",
                 "headRefOid": "first", "mergedAt": "2026-08-01T10:00:00Z"},
                {"number": 20, "headRefName": "nova/reused",
                 "headRefOid": "second", "mergedAt": "2026-08-24T10:00:00Z"}]))

    assert tidy_workspace.survey_remote_branches(["o/r"])[0]["outstanding"] == []


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/SokratesAI/agora.git", "SokratesAI/agora"),
    ("https://github.com/SokratesAI/agora", "SokratesAI/agora"),
    ("git@github.com:SokratesAI/agora-persona-runner.git",
     "SokratesAI/agora-persona-runner"),
    ("", None),
    ("notaurl", None),
])
def test_origin_urls_this_loop_actually_uses_parse(url, expected):
    assert tidy_workspace._repo_from_url(url) == expected


def test_the_sweep_prints_the_branch_and_what_is_on_it(monkeypatch, capsys):
    """The output is the whole product here -- a cycle reads this line and
    goes and looks. Reviewer-proofing the wording is not the point; that the
    branch, the count and the files all reach the page is."""
    monkeypatch.setattr(tidy_workspace, "origin_repos", lambda roots: ["o/r"])
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/killed-midway", "bbb")],
        compares={"nova/killed-midway": _compare(3, ["tools/x.py", "b.py"])}))

    tidy_workspace._sweep_remote(["/nowhere"])
    out = capsys.readouterr().out

    assert "nova/killed-midway" in out
    assert "3 commit(s) past main" in out
    assert "no PR was ever opened from it" in out
    assert "tools/x.py, b.py" in out


def test_a_repo_the_sweep_could_not_reach_says_so(monkeypatch, capsys):
    """Silence here would read as an all-clear for the one repo nobody
    checked."""
    monkeypatch.setattr(tidy_workspace, "origin_repos", lambda roots: ["o/r"])
    monkeypatch.setattr(tidy_workspace.subprocess, "run", _gh_fake(
        branches=[("nova/mine", "bbb")], broken=["/branches"]))

    tidy_workspace._sweep_remote(["/nowhere"])

    assert "could not sweep the remote" in capsys.readouterr().out
