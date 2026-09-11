"""The capture box's Add on his two boards writes the #203 record store, not
his markdown.

`nova_capture.capture` read-modified-wrote `issues.md`/`ideas.md` for every
bullet he typed. Those two boards live in the record store now, so a capture
is one new capture document per bullet, minted through the registry; only
`notes`, which has no records, still goes to the file.
"""

import agora_runner.nova_capture as nova_capture
from agora_runner import board_records, board_store
from agora_runner.nova_capture import capture

FIRST = "His first capture"
SECOND = "His second capture"
REPLY = "Nova, cycle 1: answered."


def _records(monkeypatch):
    """The issues-board fake from the Edit/Delete tests, and a vault that must
    not be touched -- Add writes the records."""
    from tests.test_board_records import writable

    _, fake = writable(board="issue")
    monkeypatch.setattr(nova_capture, "board_store", fake)

    def landmine(*a, **k):
        raise AssertionError("the capture add touched the markdown")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", landmine)
    monkeypatch.setattr(nova_capture, "vault_write_path", landmine)
    return fake


def _captures(store):
    held = board_records.contents("issue", store=store)
    return held["captures"], held["captureReplies"]


def _ids(store):
    return [doc["captureId"]
            for doc in board_records.capture_documents("issue", store=store)]


def test_a_capture_lands_below_the_ones_already_there(monkeypatch):
    store = _records(monkeypatch)
    ok, message = capture("issues", "a new thought")
    assert ok, message
    assert message == "captured to issues"
    captures, replies = _captures(store)
    assert captures == [FIRST, SECOND, "a new thought"]
    assert replies[0] == [REPLY], "an add must not lose the answer under another"


def test_a_paste_of_several_lines_is_several_records_in_the_order_typed(monkeypatch):
    store = _records(monkeypatch)
    ok, message = capture("issues", "one\n\ntwo\nthree")
    assert ok, message
    assert _captures(store)[0] == [FIRST, SECOND, "one", "two", "three"]


def test_the_rating_and_the_project_ride_on_the_first_record_only(monkeypatch):
    store = _records(monkeypatch)
    ok, message = capture("issues", "one\ntwo", priority="High", project="Marcus")
    assert ok, message
    assert _captures(store)[0][-2:] == ["\U0001f7e0 High: one #marcus", "two"]


def test_the_ids_are_new_and_the_registry_is_written_before_any_capture(monkeypatch):
    """An id the registry never recorded is one the next mint hands out again."""
    store = _records(monkeypatch)
    before = _ids(store)
    ok, message = capture("issues", "one\ntwo")
    assert ok, message
    kinds = [call[0] for call in store.calls]
    assert kinds == ["write_registry", "write_capture", "write_capture"]
    new = [call[1]["captureId"] for call in store.calls[1:]]
    assert len(set(new)) == 2 and not set(new) & set(before)
    assert set(_ids(store)) == set(before) | set(new)


def test_a_registry_conflict_mints_again_against_a_fresh_read(monkeypatch):
    store = _records(monkeypatch)
    real = store.write_registry
    reads = []
    reads_at_write = []

    def racing(registry):
        reads_at_write.append(len(reads))
        if len(reads_at_write) == 1:
            raise board_store.RegistryConflict("409 conflict")
        return real(registry)

    real_read = store.read_registry

    def reading():
        reads.append(1)
        held = real_read()
        return dict(held, captures=dict(held.get("captures", {})))

    monkeypatch.setattr(store, "write_registry", racing)
    monkeypatch.setattr(store, "read_registry", reading)
    ok, message = capture("issues", "after a race")
    assert ok, message
    assert len(reads_at_write) == 2
    assert reads_at_write[1] > reads_at_write[0], "the retry must re-read, not resend"
    assert _captures(store)[0][-1] == "after a race"


def test_a_registry_that_lags_the_stored_ids_never_reuses_one(monkeypatch):
    """The fixture's registry has high-water 0 while `cap_1`/`cap_2` are stored.
    Minting `cap_1` again would overwrite his first capture."""
    store = _records(monkeypatch)
    store.registry["captures"] = {}
    ok, message = capture("issues", "a new thought")
    assert ok, message
    assert _captures(store)[0] == [FIRST, SECOND, "a new thought"]
    assert store.registry["captures"]["issue"] == 3


def test_a_registry_that_keeps_conflicting_gives_up_and_writes_no_capture(monkeypatch):
    store = _records(monkeypatch)

    def always(registry):
        store.calls.append(("write_registry", dict(registry)))
        raise board_store.RegistryConflict("409 conflict")

    monkeypatch.setattr(store, "write_registry", always)
    ok, message = capture("issues", "never lands")
    assert not ok
    assert "409" in message
    assert [c[0] for c in store.calls] == ["write_registry"] * nova_capture.WRITE_ATTEMPTS


def test_a_write_that_fails_part_way_says_how_many_landed(monkeypatch):
    store = _records(monkeypatch)
    real = store.write_capture

    def second_fails(doc):
        if sum(1 for c in store.calls if c[0] == "write_capture") == 1:
            store.calls.append(("write_capture", dict(doc)))
            raise board_store.StoreError("500 boom")
        return real(doc)

    monkeypatch.setattr(store, "write_capture", second_fails)
    ok, message = capture("issues", "one\ntwo")
    assert not ok
    assert "1 of 2 landed" in message
    assert _captures(store)[0] == [FIRST, SECOND, "one"]


def test_a_board_migrated_with_whole_number_ranks_gets_the_next_number(monkeypatch):
    """`tools.board_migrate` stores `index + 1`, not a rank_key. A rank_key
    beside those ints makes every later read of his board a TypeError."""
    store = _records(monkeypatch)
    for index, doc in enumerate(d for d in store.docs if d.get("type") == "capture"):
        doc["rank"] = index + 1
    ok, message = capture("issues", "one\ntwo")
    assert ok, message
    assert [c[1]["rank"] for c in store.calls if c[0] == "write_capture"] == [3, 4]
    assert _captures(store)[0] == [FIRST, SECOND, "one", "two"]


def test_ideas_writes_the_idea_board(monkeypatch):
    """`RECORD_BOARDS` decides the board; a capture typed on ideas must not
    land on issues."""
    from tests.test_board_records import writable

    _, store = writable(board="idea")
    monkeypatch.setattr(nova_capture, "board_store", store)
    ok, message = capture("ideas", "an idea")
    assert ok, message
    assert board_records.contents("idea", store=store)["captures"][-1] == "an idea"
