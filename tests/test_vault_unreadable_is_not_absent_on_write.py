"""A database that will not answer must not read as an empty slot to write into.

The read side lost this conflation in #148 (see
`test_vault_unreadable_is_not_missing.py`). The write side kept it, in two
places: `vault_write_path` and the fallback lookup inside `_vault_put_raw`
both did `existing if status == 200 else None`, so a 500, a 503 or a 401 on
the pre-write lookup made a live document look absent.

It was filed as degrading safely, on the grounds that the resulting PUT
carries no `_rev` and 409s against the live document. That is true of
exactly one of the two shapes it takes, and it is the shape this loop uses
least:

- **With a real `if_rev`** -- every `put --if-rev-file`, so every journal
  entry and every digest write, plus `nova_capture`, `nova_comments` and
  `tools_dispatch` -- the revision comes from the caller, so the PUT
  *succeeds*. The only thing `existing` still carries at that point is
  `ctime`, which silently becomes "now". The write lands and quietly
  rewrites the file's creation time, and nothing anywhere says so.
- **Unconditional, or `if_rev=None`**, it does 409 -- but reports
  `FAILED(409 conflict: <path> changed since it was read)`, which is a
  false statement about what happened. Nothing changed. The database
  refused. That string is load bearing: `_write_exit` turns it into exit
  3, `vault_append_path` and `nova_comments` retry on it, and `prompt.md`
  step 7 tells a cycle that exit 3 means re-read and write again.
  Retrying is the one wrong response to a 500.

The fake here is `FakeCouch`, which enforces CouchDB's real revision rule,
with one document's GET answered by a failure status while the rest of the
database stays up. That partial shape is deliberate and is what makes these
tests exercise the branch at all: a fake that took the whole database down
would fail at the chunk PUTs and never reach the file doc.

`bridge/vault_tool.py` carries the same client against the same database
and its half of this landed in the same cycle.
"""
from unittest.mock import patch

import pytest

from agora_runner import vault
from tests.couch_fake import FakeCouch

PATH = "notes/issues.md"
BEFORE = "# Issues\n\n- one\n"
MINE = "# Issues\n\n- mine\n"


def _couch_with_unreadable(path=PATH, status=500, content=BEFORE):
    couch = FakeCouch()
    couch.seed(path, content)
    couch.unreadable[path] = status
    return couch


def test_an_unreadable_document_is_not_overwritten_as_if_it_were_absent():
    couch = _couch_with_unreadable()
    with patch.object(vault, "couch_req", couch.req):
        result = vault.vault_write_path(PATH, MINE)
    assert "unreadable" in result, result
    assert "500" in result, result
    assert couch.text(PATH) == BEFORE


def test_the_refusal_does_not_masquerade_as_a_conflict():
    """The consequence, not the wording. `409 conflict` in a write result
    means "someone else wrote first, re-read and retry" to `_write_exit`
    (exit 3), to `vault_append_path`, to `nova_comments` and to Nova's own
    instructions. A 500 answered with that string sends every one of them
    into a retry against a database that is refusing."""
    couch = _couch_with_unreadable()
    with patch.object(vault, "couch_req", couch.req):
        result = vault.vault_write_path(PATH, MINE)
    assert "409 conflict" not in result, result


def test_a_conditional_write_no_longer_lands_with_the_ctime_wiped():
    """The half that was never safe. The caller supplies `_rev`, so the PUT
    succeeds whatever the lookup said -- and `existing` is the only source
    of `ctime`, which becomes `now_ms` when it is None. Old behaviour:
    "written", creation time silently replaced. This is the real
    interleaving too: the caller's own read succeeds, and the pre-write
    lookup a moment later does not."""
    couch = FakeCouch()
    couch.seed(PATH, BEFORE)
    with patch.object(vault, "couch_req", couch.req):
        _content, rev = vault.vault_read_path_rev(PATH)
        couch.unreadable[PATH] = 500
        result = vault.vault_write_path(PATH, MINE, if_rev=rev)
    assert "unreadable" in result, result
    assert couch.text(PATH) == BEFORE
    assert couch.docs[PATH]["ctime"] == 1


def test_the_inner_lookup_refuses_on_its_own():
    """`_vault_put_raw` re-looks-up whenever it is handed `existing=None`,
    which is every call from `vault_write_path` against a file that really
    is absent, plus any direct caller. Fixing only the outer site would
    leave the same conflation one frame down."""
    couch = _couch_with_unreadable()
    with patch.object(vault, "couch_req", couch.req):
        result = vault._vault_put_raw(PATH, MINE, existing=None)
    assert "unreadable" in result, result
    assert couch.text(PATH) == BEFORE


@pytest.mark.parametrize("status", [401, 403, 500, 502, 503])
def test_every_non_404_failure_is_refused_not_just_500(status):
    couch = _couch_with_unreadable(status=status)
    with patch.object(vault, "couch_req", couch.req):
        result = vault.vault_write_path(PATH, MINE)
    assert "unreadable" in result and str(status) in result, result
    assert couch.text(PATH) == BEFORE


def test_a_genuine_404_still_creates_the_file():
    """The negative control, and the reason this is a narrowing rather than
    a new gate. 404 is a real answer -- a journal entry that has not been
    written yet, an archive not yet rolled -- and creating the file is the
    correct response to it. A fix that refused here would break every first
    write this loop makes."""
    couch = FakeCouch()
    with patch.object(vault, "couch_req", couch.req):
        assert vault.vault_write_path("notes/brand-new.md", MINE) == "written"
    assert couch.text("notes/brand-new.md") == MINE


def test_the_helper_raises_rather_than_returning_a_sentinel():
    """Absent and unreadable share one vocabulary with the read side --
    `VaultUnreadableDocument` -- and the string contract lives at the write
    entry points, which catch it. Both halves matter: a raise that escaped
    `vault_write_path` would break every caller that branches on the
    returned string, which is the finding #148's reviewer caught about this
    same exception class."""
    couch = _couch_with_unreadable()
    with patch.object(vault, "couch_req", couch.req):
        with pytest.raises(vault.VaultUnreadableDocument) as excinfo:
            vault._doc_to_overwrite(PATH)
        assert vault._doc_to_overwrite("notes/nothing-here.md") is None
    assert "500" in str(excinfo.value)
