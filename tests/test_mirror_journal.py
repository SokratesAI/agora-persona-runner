"""`tools/mirror_journal.py` -- fetching only the entries not on disk.

The one property worth pinning is that a document already mirrored is
never fetched again: an entry is written once and never edited, and the
whole point of the tool is that a cycle checking against the real
journal pays for two documents rather than a hundred and seventy. So
the fetcher here counts its calls, the way `test_nova_journal.py` counts
renders -- the returned file set is identical whether or not the
skipping works, so only the count can see it.
"""

import pytest

from tools.mirror_journal import mirror, plan


def test_only_the_names_the_vault_has_and_disk_does_not():
    assert plan(["a.md", "b.md"], ["a.md", "b.md", "c.md"]) == (["c.md"], [])


def test_a_document_on_disk_that_the_vault_lost_is_reported():
    assert plan(["a.md", "b.md"], ["a.md"]) == ([], ["b.md"])


def _fetcher(calls, body="### Cycle 1\n"):
    def fetch(name, path):
        calls.append(name)
        path.write_text(body)
    return fetch


def test_an_entry_already_mirrored_is_not_fetched_again(tmp_path):
    (tmp_path / "001-cycle-3.md").write_text("### Cycle 3\n")
    calls = []
    fetched, failed, orphans = mirror(
        tmp_path, lambda: ["001-cycle-3.md", "002-cycle-4.md"], _fetcher(calls))

    assert calls == ["002-cycle-4.md"], "re-fetched an entry it already had"
    assert (fetched, failed, orphans) == (["002-cycle-4.md"], [], [])
    assert (tmp_path / "001-cycle-3.md").read_text() == "### Cycle 3\n"


def test_a_second_run_fetches_nothing(tmp_path):
    calls = []
    listing = lambda: ["001-cycle-3.md", "002-cycle-4.md"]
    mirror(tmp_path, listing, _fetcher(calls))
    mirror(tmp_path, listing, _fetcher(calls))
    assert calls == ["001-cycle-3.md", "002-cycle-4.md"]


def test_one_failed_document_does_not_cost_the_others(tmp_path):
    def fetch(name, path):
        if name == "002-cycle-4.md":
            raise RuntimeError("[not found]")
        path.write_text("### Cycle\n")

    fetched, failed, orphans = mirror(
        tmp_path,
        lambda: ["001-cycle-3.md", "002-cycle-4.md", "003-cycle-5.md"],
        fetch)

    assert fetched == ["001-cycle-3.md", "003-cycle-5.md"]
    assert failed == [("002-cycle-4.md", "[not found]")]
    assert not (tmp_path / "002-cycle-4.md").exists(), (
        "a failed fetch left a file behind, so the next run would skip it")


def test_the_directory_is_created_on_a_first_run(tmp_path):
    target = tmp_path / "journal-mirror"
    fetched, failed, orphans = mirror(
        target, lambda: ["001-cycle-3.md"], _fetcher([]))
    assert fetched == ["001-cycle-3.md"]
    assert (target / "001-cycle-3.md").exists()
