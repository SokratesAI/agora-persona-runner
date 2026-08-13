"""The workspace sweep only ever touches names it made itself.

`/data/workspace` holds nine git clones, this repo among them, and the sweep
runs unattended at the end of a cycle. So the tests that matter most here are
the ones asserting what it leaves alone -- a clone with uncommitted work in it
is not recoverable from anywhere, and no amount of correct archiving would pay
for deleting one.

Every test runs against a `tmp_path`, never the real workspace.
"""
import os

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


def test_a_reviewer_worktree_is_removed(workspace):
    """`review-rubric.md` says to make one every build cycle and to remove it
    afterwards; four were still on disk when this script was written, which
    is the measurement that says the manual step does not happen.
    """
    (workspace / "_review-c178").mkdir()
    (workspace / "_review-c178" / "checkout.py").write_text("x", encoding="utf-8")

    _, _, worktrees = tidy_workspace.tidy(
        str(workspace), today="2026-08-14",
        clone=str(workspace / "no-such-clone"))

    assert worktrees == ["_review-c178"]
    assert not (workspace / "_review-c178").exists()


def test_a_worktree_git_refuses_to_remove_still_goes(workspace, monkeypatch):
    """The fallback. `git worktree remove` fails whenever the clone no longer
    knows about the worktree -- re-cloned, or made from a different checkout
    -- and a script that stopped there would leave exactly the litter it
    exists to clear. The subprocess is stubbed rather than a real repo built,
    because what is under test is what happens when it does nothing.
    """
    calls = []
    monkeypatch.setattr(tidy_workspace.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]))
    (workspace / "_review-c178").mkdir()
    (workspace / "agora-persona-runner" / "worktrees").mkdir()

    tidy_workspace.tidy(str(workspace), today="2026-08-14",
                        clone=str(workspace / "agora-persona-runner"))

    assert not (workspace / "_review-c178").exists()
    assert any("remove" in call for call in calls), calls
    assert any("prune" in call for call in calls), calls


def test_dry_run_changes_nothing(workspace):
    """It reports the same three lists it would act on, so the preview is the
    plan rather than a second implementation of it."""
    (workspace / "_scratch-archive-2026-08-01").mkdir()
    (workspace / "_review-c178").mkdir()
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
