"""The workspace sweep only ever touches names it made itself.

`/data/workspace` holds nine git clones, this repo among them, and the sweep
runs unattended at the end of a cycle. So the tests that matter most here are
the ones asserting what it leaves alone -- a clone with uncommitted work in it
is not recoverable from anywhere, and no amount of correct archiving would pay
for deleting one.

Every test runs against a `tmp_path`, never the real workspace.
"""
import os
import subprocess
import time

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
    assert "repo: branch nova/feature is already on origin/main" in out
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
