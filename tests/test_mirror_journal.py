"""`tools/mirror_journal.py` -- fetching only the entries not on disk.

The property nothing else can see is that a document already mirrored
is never fetched again: an entry is written once and never edited, and the
whole point of the tool is that a cycle checking against the real
journal pays for two documents rather than a hundred and seventy. So
the fetcher here counts its calls, the way `test_nova_journal.py` counts
renders -- the returned file set is identical whether or not the
skipping works, so only the count can see it.
"""

from pathlib import Path

import pytest

from tools.mirror_journal import _fetcher, mirror, plan


def test_only_the_names_the_vault_has_and_disk_does_not():
    assert plan(["a.md", "b.md"], ["a.md", "b.md", "c.md"]) == (["c.md"], [])


def test_a_document_on_disk_that_the_vault_lost_is_reported():
    assert plan(["a.md", "b.md"], ["a.md"]) == ([], ["b.md"])


def _counting_fetcher(calls, body="### Cycle 1\n"):
    def fetch(name, path):
        calls.append(name)
        path.write_text(body)
    return fetch


def test_an_entry_already_mirrored_is_not_fetched_again(tmp_path):
    (tmp_path / "001-cycle-3.md").write_text("### Cycle 3\n")
    calls = []
    fetched, failed, orphans = mirror(
        tmp_path, lambda: ["001-cycle-3.md", "002-cycle-4.md"], _counting_fetcher(calls))

    assert calls == ["002-cycle-4.md"], "re-fetched an entry it already had"
    assert (fetched, failed, orphans) == (["002-cycle-4.md"], [], [])
    assert (tmp_path / "001-cycle-3.md").read_text() == "### Cycle 3\n"


def test_a_second_run_fetches_nothing(tmp_path):
    calls = []
    listing = lambda: ["001-cycle-3.md", "002-cycle-4.md"]
    mirror(tmp_path, listing, _counting_fetcher(calls))
    mirror(tmp_path, listing, _counting_fetcher(calls))
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


def _stub_vault_tool(tmp_path, body):
    """A stand-in for `vault_tool.py get`, which exits 0 either way.

    The real one prints its own not-found line and still succeeds, so
    `check=True` sees nothing wrong and the body is the only signal
    there is. Anything reading this contract has to be tested against a
    process, not a callable.
    """
    tool = tmp_path / "stub_vault_tool.py"
    tool.write_text("import sys\nsys.stdout.write(%r)\n" % body)
    return str(tool)


def test_a_document_that_did_not_come_back_leaves_no_file(tmp_path):
    fetch = _fetcher(_stub_vault_tool(tmp_path, "[not found] nova/journal/x.md\n"),
                     "projects/x/")
    with pytest.raises(RuntimeError):
        fetch("001-cycle-3.md", tmp_path / "001-cycle-3.md")
    assert not (tmp_path / "001-cycle-3.md").exists(), (
        "a failed fetch left a file behind, so every later run skips it")
    assert not list(tmp_path.glob("*.part"))


def test_a_document_that_came_back_is_written_whole(tmp_path):
    fetch = _fetcher(_stub_vault_tool(tmp_path, "### Cycle 3\n\nreal.\n"),
                     "projects/x/")
    fetch("001-cycle-3.md", tmp_path / "001-cycle-3.md")
    assert (tmp_path / "001-cycle-3.md").read_text() == "### Cycle 3\n\nreal.\n"
    assert not list(tmp_path.glob("*.part"))


def test_a_write_that_dies_halfway_leaves_nothing_to_skip(monkeypatch, tmp_path):
    """The reason the body goes through a temp name.

    A cycle killed mid-fetch is the case this tool cannot survive any
    other way: a half-written entry is indistinguishable from a finished
    one, so every later run skips it and every measurement taken against
    the mirror is quietly wrong. The failure is injected rather than
    waited for -- nothing else here can produce a torn write.
    """
    real = Path.write_text

    def torn(self, text, *args, **kwargs):
        real(self, text[:5])
        raise OSError("no space left on device")

    fetch = _fetcher(_stub_vault_tool(tmp_path, "### Cycle 3\n\nreal.\n"),
                     "projects/x/")
    monkeypatch.setattr(Path, "write_text", torn)  # after the stub is written
    with pytest.raises(OSError):
        fetch("001-cycle-3.md", tmp_path / "001-cycle-3.md")
    assert not (tmp_path / "001-cycle-3.md").exists(), (
        "a torn write left a document the next run will treat as finished")


def test_the_directory_is_created_on_a_first_run(tmp_path):
    target = tmp_path / "journal-mirror"
    fetched, failed, orphans = mirror(
        target, lambda: ["001-cycle-3.md"], _counting_fetcher([]))
    assert fetched == ["001-cycle-3.md"]
    assert (target / "001-cycle-3.md").exists()
