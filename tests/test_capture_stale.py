"""`tools.capture_stale` against a real git repository.

The pure rule is pinned in `tests/test_capture_symbols.py`. What is left
here is the half that can only be wrong against git: the date this reads
must be the commit that **introduced** the symbol, not the newest one that
touched it, and the grep gate must not swallow a symbol that is really
there.
"""

import subprocess

import pytest

from tools import capture_stale


def _run(repo, *args, env=None):
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                   capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path):
    """A repo where `oldSymbol` lands first and `newSymbol` lands later."""
    root = tmp_path / "demo"
    root.mkdir()
    _run(root, "init", "-q", "-b", "main")
    _run(root, "config", "user.email", "nova@example.com")
    _run(root, "config", "user.name", "Nova")
    src = root / "app.js"

    src.write_text("function oldSymbol() { return 1; }\n")
    _run(root, "add", "app.js")
    _run(root, "commit", "-q", "-m", "old", "--date", "2026-09-01T10:00:00")

    src.write_text("function oldSymbol() { return 2; }\n"
                   "function newSymbol() { return 3; }\n")
    _run(root, "add", "app.js")
    _run(root, "commit", "-q", "-m", "new", "--date", "2026-09-12T10:00:00")

    # A third commit that ADDS A SECOND OCCURRENCE of oldSymbol, so that
    # `git log -S` really lists two commits for it and "newest" and
    # "introduced" give different answers. Merely editing the line around
    # it is not enough -- `-S` counts occurrences, so a commit that leaves
    # the count unchanged never appears in that log at all, and a fixture
    # built that way lets the wrong index pass.
    src.write_text("function oldSymbol() { return 4; }\n"
                   "function newSymbol() { return oldSymbol(); }\n")
    _run(root, "add", "app.js")
    _run(root, "commit", "-q", "-m", "touch old again",
         "--date", "2026-09-13T10:00:00")
    return root


class TestFirstSeen:
    def test_it_reads_the_introducing_commit_not_the_newest_one(self, repo):
        # oldSymbol gained a second occurrence on 09-13, so `git log -S`
        # lists 09-13 and 09-01 for it. If this returned the newest of the
        # two, every note written before today would read as a finding.
        log = subprocess.run(["git", "-C", str(repo), "log", "-S", "oldSymbol",
                              "--format=%as"], capture_output=True, text=True)
        assert log.stdout.split() == ["2026-09-13", "2026-09-01"], (
            "fixture no longer distinguishes newest from introducing")
        assert capture_stale.first_seen(repo, "oldSymbol") == ("2026-09-01", "demo")

    def test_a_later_symbol_reports_its_own_first_commit(self, repo):
        assert capture_stale.first_seen(repo, "newSymbol") == ("2026-09-12", "demo")

    def test_a_symbol_nothing_carries_is_none(self, repo):
        assert capture_stale.first_seen(repo, "neverBuilt") is None

    def test_a_substring_of_a_real_symbol_does_not_count(self, repo):
        # `-w` on the grep: `Symbol` appears inside both names and is not a
        # symbol this repo defines. Without the word boundary every short
        # note fragment would match something.
        assert capture_stale.first_seen(repo, "Symbol") is None


class TestJudge:
    def test_a_note_older_than_the_symbol_it_names_is_the_finding(self, repo):
        notes = [{"date": "2026-09-11", "cycle": 1419,
                  "text": "nothing computes `newSymbol` yet"}]
        rows, skipped = capture_stale.judge(notes, [repo])
        assert skipped == 0
        assert rows[0]["verdict"] == "BUILT AFTER"
        assert rows[0]["symbols"][0]["first_seen"] == "2026-09-12"
        assert rows[0]["symbols"][0]["where"] == "demo"

    def test_a_note_naming_an_older_symbol_is_quiet(self, repo):
        notes = [{"date": "2026-09-11", "cycle": 1419,
                  "text": "`oldSymbol` needs a lever"}]
        rows, _ = capture_stale.judge(notes, [repo])
        assert rows[0]["verdict"] == "PREDATES"

    def test_a_note_naming_nothing_built_stays_absent(self, repo):
        notes = [{"date": "2026-09-11", "text": "we still need `neverBuilt`"}]
        rows, _ = capture_stale.judge(notes, [repo])
        assert rows[0]["verdict"] == "ABSENT"

    def test_a_note_with_no_identifier_is_counted_not_judged(self, repo):
        notes = [{"date": "2026-09-11", "text": "the plan holds no weight"}]
        rows, skipped = capture_stale.judge(notes, [repo])
        assert rows == []
        assert skipped == 1

    def test_one_symbol_is_looked_up_once_across_many_notes(self, repo, monkeypatch):
        calls = []
        real = capture_stale.first_seen

        def counted(r, s):
            calls.append(s)
            return real(r, s)

        monkeypatch.setattr(capture_stale, "first_seen", counted)
        notes = [{"date": "2026-09-11", "text": "`newSymbol` a"},
                 {"date": "2026-09-10", "text": "`newSymbol` b"}]
        rows, _ = capture_stale.judge(notes, [repo])
        assert [r["verdict"] for r in rows] == ["BUILT AFTER", "BUILT AFTER"]
        assert calls == ["newSymbol"]


class TestCheckouts:
    def test_it_finds_a_git_directory_and_skips_a_plain_one(self, repo, tmp_path):
        (tmp_path / "not-a-repo").mkdir()
        found = capture_stale.checkouts(tmp_path)
        assert [p.name for p in found] == ["demo"]

    def test_a_missing_workspace_is_empty_rather_than_an_exception(self, tmp_path):
        assert capture_stale.checkouts(tmp_path / "nope") == []


class TestMain:
    def test_it_reads_local_files_and_reports(self, repo, tmp_path, capsys):
        page = tmp_path / "issues.md"
        page.write_text("# Issues\n\n## Entries\n\n"
                        "- 2026-09-11 (Cycle 1419) — nothing computes `newSymbol` yet\n")
        empty = tmp_path / "ideas.md"
        empty.write_text("# Ideas\n\n## Entries\n\n")
        code = capture_stale.main(["--issues", str(page), "--ideas", str(empty),
                                   "--workspace", str(repo.parent)])
        out = capsys.readouterr().out
        assert "`newSymbol` first committed 2026-09-12 in demo" in out
        # Advisory: a finding is printed and does not raise the status.
        assert code == 0

    def test_an_unreadable_capture_file_exits_one(self, repo, tmp_path, capsys):
        empty = tmp_path / "ideas.md"
        empty.write_text("# Ideas\n\n## Entries\n\n")
        code = capture_stale.main(["--issues", str(tmp_path / "gone.md"),
                                   "--ideas", str(empty),
                                   "--workspace", str(repo.parent)])
        assert code == 1

    def test_no_checkout_at_all_is_an_error_not_a_clean_sweep(self, tmp_path, capsys):
        code = capture_stale.main(["--workspace", str(tmp_path / "nothing")])
        assert code == 1
        assert "cannot see a single checkout" in capsys.readouterr().err


class TestOldestSighting:
    def test_a_later_copy_in_another_repo_does_not_win(self, repo, tmp_path):
        """The defect the live run found: first repo alphabetically, not oldest.

        `poke_page` had been in the runner for weeks and was copied into the
        bridge on 09-11. Walking the checkouts in name order reached the copy
        first and reported its date, which turned a true note from 08-31 into
        a finding.
        """
        later = tmp_path / "zz-copy"
        later.mkdir()
        _run(later, "init", "-q", "-b", "main")
        _run(later, "config", "user.email", "nova@example.com")
        _run(later, "config", "user.name", "Nova")
        (later / "copy.js").write_text("function oldSymbol() { return 9; }\n")
        _run(later, "add", "copy.js")
        _run(later, "commit", "-q", "-m", "copied", "--date", "2026-09-14T10:00:00")

        # Ordered so the NEWER copy is asked first -- the order that broke it.
        assert capture_stale.oldest_sighting([later, repo], "oldSymbol") == (
            "2026-09-01", "demo")

    def test_a_symbol_in_no_repo_is_none(self, repo):
        assert capture_stale.oldest_sighting([repo], "neverBuilt") is None
