"""`agora_runner.board_publish` -- the site redraws his board file itself.

Issue #203 made his `issues.md` / `ideas.md` a view of the records, and
until this the view was only ever drawn by a cycle typing
`tools.board_publish --publish`, so it fell behind at his first tap in the
app. The cases here are the three things that wiring has to get right: the
publish works over the site's vault client (two functions, not the bridge's
subprocess), every board write reaches it through `nova_site.invalidate`,
and nothing is published until the site's `main` starts it -- because
`invalidate` is called by dozens of tests and a self-starting worker would
have them drawing into the real vault.

Same fake CouchDB as `test_tools_board_publish.py`, for the same reason: a
fake `board_store` would let a render that never reached a database pass.
"""
import threading

import pytest

from agora_runner import board_publish, board_records, nova_site, ticket_docs
from tools import board_migrate

from tests.test_board_store import FakeCouch
from tests.test_tools_board_migrate import board

FRONTMATTER = "---\ntype: log\ncontract: his words.\n---\n"


@pytest.fixture
def couch(monkeypatch):
    fake = FakeCouch()
    monkeypatch.setattr(ticket_docs, "_req", fake)
    return fake


class DictVault:
    """The site's view of the vault: `read` -> (text, rev), `write` a
    compare-and-swap that refuses a revision that has moved."""

    def __init__(self, text, rev="5-a"):
        self.text, self.rev, self.writes = text, rev, 0
        self.fail = False

    def read(self, path):
        assert path == board_publish.VAULT_PATHS["issue"]
        return self.text, self.rev

    def write(self, path, text, rev):
        self.writes += 1
        if self.fail or rev != self.rev:
            return False, "FAILED(409 conflict)"
        self.text = text
        self.rev = f"{int(self.rev.split('-')[0]) + 1}-b"
        return True, "written"


@pytest.fixture
def vault(couch):
    markdown = FRONTMATTER + board(
        [(1, "Nova", ""), (2, "Marcus", "v1")], details=[(1, "why row 1")])
    board_migrate.migrate(markdown, "issue", apply=True)
    return DictVault(markdown + "\na line the records never held\n")


def test_publish_over_two_functions_writes_the_view_and_stamps_it(vault):
    code, lines = board_publish.publish("issue", vault.read, vault.write)

    assert code == 0, lines
    assert "a line the records never held" not in vault.text
    assert "why row 1" in vault.text
    assert board_records.currency("issue", vault.rev)[0] == board_records.CURRENT


def test_a_refused_write_is_not_stamped(vault):
    before = board_records.stored_source_rev("issue")
    vault.fail = True

    code, lines = board_publish.publish("issue", vault.read, vault.write)

    assert code == 3, lines
    assert "FAILED(409 conflict)" in lines
    assert board_records.stored_source_rev("issue") == before


def test_request_does_nothing_until_the_site_starts_the_publisher(monkeypatch):
    """The guard that keeps every test calling `invalidate` off the vault."""
    monkeypatch.setattr(board_publish, "_publisher", None)

    assert board_publish.request("issue") is False


def test_a_write_to_his_board_asks_for_a_publish(monkeypatch):
    asked = []
    monkeypatch.setattr(board_publish, "request", asked.append)

    nova_site.invalidate("board:issues")
    nova_site.invalidate("board:ideas")
    nova_site.invalidate("board:notes")
    nova_site.invalidate("plan")

    assert asked == ["issue", "idea"]


def test_the_site_write_adapter_reports_a_conflict_as_a_failure(monkeypatch):
    monkeypatch.setattr(nova_site, "vault_write_path",
                        lambda path, text, if_rev: "FAILED(409 conflict: x)")
    assert nova_site._publish_write("p", "t", "1-a") == (
        False, "FAILED(409 conflict: x)")

    sent = {}
    monkeypatch.setattr(nova_site, "vault_write_path",
                        lambda path, text, if_rev: sent.update(rev=if_rev)
                        or "written")
    assert nova_site._publish_write("p", "t", "1-a") == (True, "written")
    assert sent == {"rev": "1-a"}


def test_a_burst_of_writes_is_one_publish(vault):
    publisher = board_publish.Publisher(vault.read, vault.write,
                                        log=lambda line: None)
    for _ in range(3):
        publisher.request("issue")

    publisher.drain()

    assert vault.writes == 1
    assert publisher.pending == set()


def test_a_failed_publish_is_retried_and_then_given_up(vault):
    vault.fail = True
    logged = []
    publisher = board_publish.Publisher(vault.read, vault.write,
                                        log=logged.append)
    publisher.request("issue")

    for attempt in range(board_publish.MAX_RETRIES):
        assert publisher.pending == {"issue"}, attempt
        publisher.drain()

    assert publisher.pending == set()
    assert vault.writes == board_publish.MAX_RETRIES
    assert "gave up" in logged[-1]


def test_the_thread_waits_for_quiet_before_publishing(monkeypatch):
    calls, done = [], threading.Event()

    def fake_publish(board, read, write, store=None):
        calls.append(board)
        done.set()
        return 0, ["ok"]

    monkeypatch.setattr(board_publish, "publish", fake_publish)
    publisher = board_publish.Publisher(None, None, log=lambda line: None,
                                        debounce=0.2)
    worker = threading.Thread(target=publisher.run, daemon=True)
    worker.start()
    try:
        # Spaced wider than the thread needs to wake and narrower than the
        # debounce: without the quiet wait each of these is its own publish.
        for _ in range(3):
            publisher.request("issue")
            assert not done.wait(0.08)

        assert done.wait(5)
        done.clear()
        assert not done.wait(0.5)
        assert calls == ["issue"]
    finally:
        publisher.stop()
        worker.join(5)
    assert not worker.is_alive()
