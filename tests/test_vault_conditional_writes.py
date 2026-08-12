"""Optimistic concurrency on the vault write path (idea #63, slice 1).

Every write this loop makes is a read-modify-write, and until this change
the revision the caller read at was thrown away: `vault_write_path` looked
up a *fresh* `_rev` immediately before the PUT. Two writers overlapping did
not conflict, did not error and did not retry -- the second simply won, and
the first one's work was gone with no trace anywhere.

The fake below is a real revision store rather than a stub, because that is
the only way to test this honestly. A fake that returns whatever status the
test wants proves the code branches on 409; it does not prove CouchDB would
ever send one. This one applies CouchDB's actual rule -- a PUT whose `_rev`
does not match the stored one is rejected -- so the tests fail if the client
stops carrying the revision, which is exactly the bug.

`bridge/vault_tool.py` in agora-claude-bridge carries the same client
against the same database; its half of this lives in
`tests/test_vault_conditional_writes.py` there.
"""
import urllib.parse
from unittest.mock import patch

from agora_runner import nova_capture, vault


class FakeCouch:
    """A CouchDB that enforces `_rev`, and counts the writes it accepted."""

    def __init__(self, docs=None):
        self.docs = dict(docs or {})
        self.rejected = 0
        self.accepted = 0
        #: called with the doc id just before a file doc is stored, so a
        #: test can simulate another writer landing mid-flight.
        self.on_put = None

    def _next_rev(self, doc_id):
        current = self.docs.get(doc_id, {}).get("_rev", "0-x")
        return f"{int(current.split('-')[0]) + 1}-x"

    def store(self, doc_id, doc):
        """Write bypassing the revision check -- 'the other writer'."""
        doc = dict(doc)
        doc["_rev"] = self._next_rev(doc_id)
        self.docs[doc_id] = doc
        return doc["_rev"]

    def req(self, method, path, body=None):
        _db, _, rest = path.partition("/")
        rest = urllib.parse.unquote(rest)
        if method == "POST" and rest.startswith("_all_docs"):
            return 200, {"rows": [
                {"key": k, "id": k, "value": {"rev": self.docs[k]["_rev"]},
                 "doc": dict(self.docs[k])}
                if k in self.docs else {"key": k, "error": "not_found"}
                for k in body["keys"]
            ]}
        if method == "GET":
            if rest in self.docs:
                return 200, dict(self.docs[rest])
            return 404, {"error": "not_found"}
        if method == "PUT":
            if not rest.startswith("h:") and self.on_put is not None:
                hook, self.on_put = self.on_put, None
                hook(self)
            sent = (body or {}).get("_rev")
            held = self.docs.get(rest, {}).get("_rev")
            if sent != held:
                self.rejected += 1
                return 409, {"error": "conflict"}
            stored = {k: v for k, v in body.items() if k != "_rev"}
            stored["_rev"] = self._next_rev(rest)
            self.docs[rest] = stored
            self.accepted += 1
            return 201, {"ok": True}
        raise AssertionError(f"unexpected {method} {path}")

    def text(self, doc_id):
        doc = self.docs[doc_id]
        return "".join(self.docs[c]["data"] for c in doc.get("children", []))

    def seed(self, doc_id, content):
        """Put `content` at `doc_id` the way another writer would -- bypassing
        the revision check, and *advancing* the revision. Not advancing it is
        the one mistake that makes every test here pass for the wrong reason:
        a conflict is a moved revision, so a fake other-writer that leaves the
        revision alone conflicts with nobody."""
        ids = []
        for text in vault._split_chunks(content):
            chunk_id = vault._chunk_id_for(text.encode("utf-8"))
            self.docs.setdefault(
                chunk_id, {"_id": chunk_id, "data": text, "_rev": "1-x"})
            ids.append(chunk_id)
        self.store(doc_id, {"_id": doc_id, "path": doc_id, "children": ids,
                            "ctime": 1})


PATH = "notes/issues.md"


def test_read_hands_back_the_revision_it_read_at():
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n- one\n")
    with patch.object(vault, "couch_req", couch.req):
        content, rev = vault.vault_read_path_rev(PATH)
    assert content == "# Issues\n\n- one\n"
    assert rev == "1-x"


def test_a_missing_file_and_a_tombstone_are_not_the_same_answer():
    """Both have no content. Only one has a revision, and writing over a
    tombstone has to carry it or the write 409s forever."""
    couch = FakeCouch()
    couch.seed(PATH, "gone\n")
    couch.docs[PATH]["deleted"] = True
    with patch.object(vault, "couch_req", couch.req):
        assert vault.vault_read_path_rev(PATH) == (None, "1-x")
        assert vault.vault_read_path_rev("notes/never.md") == (None, None)


def test_a_write_carrying_a_stale_revision_loses_instead_of_winning():
    """The whole point. Read, someone else writes, our write must fail --
    and must not have touched their text."""
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n- one\n")
    with patch.object(vault, "couch_req", couch.req):
        _content, rev = vault.vault_read_path_rev(PATH)
        couch.seed(PATH, "# Issues\n\n- one\n- theirs\n")  # the other writer
        result = vault.vault_write_path(PATH, "# Issues\n\n- mine\n", if_rev=rev)
    assert "409 conflict" in result, result
    assert couch.text(PATH) == "# Issues\n\n- one\n- theirs\n"


def test_the_default_write_is_still_an_unconditional_overwrite():
    """Every existing caller passes no `if_rev` and must be unaffected --
    a conditional-by-default write would start failing writes that have
    always succeeded, which is a worse bug than the one being fixed."""
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n- one\n")
    with patch.object(vault, "couch_req", couch.req):
        assert vault.vault_write_path(PATH, "# Issues\n\n- mine\n") == "written"
    assert couch.text(PATH) == "# Issues\n\n- mine\n"


def test_if_rev_none_means_this_file_should_not_exist_yet():
    couch = FakeCouch()
    with patch.object(vault, "couch_req", couch.req):
        assert vault.vault_write_path(PATH, "fresh\n", if_rev=None) == "written"
        clash = vault.vault_write_path(PATH, "clobber\n", if_rev=None)
    assert "409 conflict" in clash, clash
    assert couch.text(PATH) == "fresh\n"


def test_append_that_loses_a_race_re_reads_and_keeps_both_lines():
    """A retry that resent the same body would restore the clobber in a
    loop. The merge has to be redone against the text that won."""
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n## Entries\n\n- old\n")

    def other_writer(c):
        c.seed(PATH, "# Issues\n\n## Entries\n\n- theirs\n- old\n")

    couch.on_put = other_writer
    with patch.object(vault, "couch_req", couch.req):
        result = vault.vault_append_path(PATH, "- mine", "## Entries")

    assert result == "written", result
    assert couch.rejected == 1, "the first attempt should have been rejected"
    final = couch.text(PATH)
    assert "- theirs" in final and "- mine" in final, final


def test_append_gives_up_after_a_bounded_number_of_attempts():
    """A writer that always wins must not spin forever, and the caller has
    to hear that it lost rather than that it succeeded."""
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n## Entries\n\n- old\n")
    counter = {"n": 0}

    def always_lose(c):
        counter["n"] += 1
        c.seed(PATH, f"# Issues\n\n## Entries\n\n- theirs {counter['n']}\n")
        c.on_put = always_lose

    couch.on_put = always_lose
    with patch.object(vault, "couch_req", couch.req):
        result = vault.vault_append_path(PATH, "- mine", "## Entries")

    assert "409 conflict" in result, result
    assert couch.rejected == vault.APPEND_ATTEMPTS == 3
    assert "- mine" not in couch.text(PATH)


def test_a_missing_marker_still_fails_before_writing_anything():
    """The retry loop must not turn a caller error into three of them."""
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n- old\n")
    with patch.object(vault, "couch_req", couch.req):
        result = vault.vault_append_path(PATH, "- mine", "## Nope")
    assert "after_marker not found" in result
    assert couch.accepted == 0


def test_the_capture_box_sends_the_revision_it_read():
    """nova_capture's 409 retry has existed since #66 and could never fire,
    because the write refetched the revision. This is what connects them."""
    couch = FakeCouch()
    issues = "---\ntype: log\n---\n\n- \n\n## Board\n"
    couch.seed(nova_capture.CAPTURE_TARGETS["issues"], issues)
    with patch.object(vault, "couch_req", couch.req):
        ok, message = nova_capture.capture("issues", "a new capture")
    assert ok, message
    assert "- a new capture" in couch.text(nova_capture.CAPTURE_TARGETS["issues"])


def test_a_capture_that_loses_a_race_is_not_lost():
    """Edvard typing on his phone while a cycle boards the same file. Both
    lines have to survive; before this the cycle's write silently won."""
    path = nova_capture.CAPTURE_TARGETS["issues"]
    couch = FakeCouch()
    couch.seed(path, "---\ntype: log\n---\n\n- \n\n## Board\n")

    def cycle_boards_it(c):
        c.seed(path, "---\ntype: log\n---\n\n- something a cycle added\n- \n\n## Board\n")

    couch.on_put = cycle_boards_it
    with patch.object(vault, "couch_req", couch.req):
        ok, message = nova_capture.capture("issues", "typed on the phone")

    assert ok, message
    final = couch.text(path)
    assert "- something a cycle added" in final, final
    assert "- typed on the phone" in final, final
