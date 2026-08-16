"""A blind read must not read as a short file, and a short write must not
silently replace a long document.

On 2026-08-15 a cycle read Edvard's 123,586-byte `issues.md`, got an empty
body and exit 0, and wrote the empty result back over the document — which
was still fully intact underneath. It was recovered from a local copy
within the minute, but nothing in the client objected at any point. The
revision guard did not fire and could not: the write carried the correct
`_rev` and was, to CouchDB, an ordinary edit by the only writer in the
room.

Two independent guards, because they fail in opposite directions and
either one alone leaves the door open.

`_size_checked` is the read half. A LiveSync file doc records `size`, the
byte length of the text it stands for, and every writer sets it — this
module at `_vault_put_raw`, the bridge's client, and Obsidian itself.
Measured 2026-08-15 across 37 documents (Edvard's phone-written captures,
this loop's journal entries, the JSON ledgers, the 291KB frozen archive):
`size` equalled `len(content.encode())` exactly 37 times out of 37, and no
document lacked the field. It is a length checksum the vault has always
carried and nothing has ever read. `VaultIncompleteDocument` already
catches a chunk that is *absent*; this catches a document that assembles
to the wrong length for any other reason, and the shortest such case — no
children, no data — is exactly the one that took the boards out.

`_collapse_refusal` is the write half, and it is the one that would have
stopped the actual loss. It does not care why the body is short.

The two clients are hand-synced with nothing detecting drift, so this file
is deliberately the same shape as the bridge's copy at
`tests/test_vault_collapse_guard.py` there — a diff between them should
read as "module function vs class method" and nothing else.
"""
import pytest

from agora_runner import vault

PATH = "projects/sokrates/projects/nova/issues.md"
#: The real file, at the size it actually was when it was lost.
REAL_SIZE = 123586

#: A real binary attachment, at the two lengths it actually reported.
PDF_PATH = (
    "work/platform/resources/reports/"
    "product as a product- the key to platform engineering success.pdf"
)


def doc(**kw):
    base = {"_id": PATH, "path": PATH, "_rev": "7-abc", "children": [], "data": ""}
    base.update(kw)
    return base


# --------------------------------------------------------------------- read


def test_a_document_matching_its_own_size_still_reads():
    """The control. Without it every assertion below would pass against a
    module that had simply started raising on everything."""
    assert vault.vault_assemble(doc(data="hello", size=5), path=PATH) == "hello"


def test_the_blind_read_that_lost_the_boards_now_raises():
    """The exact shape: no children, no data, and a `size` saying 123,586
    bytes should be there. This returned `""` and exit 0."""
    with pytest.raises(vault.VaultIncompleteDocument) as excinfo:
        vault.vault_assemble(doc(size=REAL_SIZE), path=PATH)
    message = str(excinfo.value)
    assert PATH in message
    # Both numbers, because the reader's next question is how much is gone.
    assert str(REAL_SIZE) in message
    assert "0 bytes" in message


def test_a_genuinely_empty_document_is_not_an_error():
    """The boundary the fix above must not break. An empty file records
    `size: 0`, agrees with itself, and is a legitimate read."""
    assert vault.vault_assemble(doc(size=0), path=PATH) == ""


def test_a_document_with_no_size_field_still_reads():
    """`size` is present on all 37 documents measured, but a client that
    refused to read anything without it would turn a field this code has
    never depended on into a hard requirement, on a vault that three
    separate writers write to."""
    assert vault.vault_assemble(doc(data="hello"), path=PATH) == "hello"


def test_size_is_bytes_not_characters():
    """The measurement said bytes. A non-ASCII document is where those two
    diverge, and getting it backwards would raise on every file Edvard
    writes an em-dash into — which is most of them."""
    text = "Skøyen — Oslo"
    assert len(text.encode("utf-8")) != len(text)
    assert vault.vault_assemble(
        doc(data=text, size=len(text.encode("utf-8"))), path=PATH) == text


def test_a_binary_attachment_is_not_checked_against_its_decoded_size():
    """`size` on a `type: newnote` doc is the *decoded* byte count while
    the chunks hold base64, so the two differ by 4/3 by construction and
    the check raised on every binary in the vault. The numbers are real —
    one of the four PDFs that took `vault_search` down vault-wide."""
    encoded = "A" * 662428
    assert vault.vault_assemble(
        doc(data=encoded, size=496813, type="newnote"), path=PDF_PATH
    ) == encoded


def test_a_document_that_declares_plain_is_still_checked():
    """The exemption is for binaries only. A doc saying `plain` that
    assembles short is the Cycle 211 failure and must still raise — the
    no-`type` case is pinned by the blind-read test above."""
    with pytest.raises(vault.VaultIncompleteDocument):
        vault.vault_assemble(doc(size=REAL_SIZE, type="plain"), path=PATH)


def test_a_chunked_document_that_assembles_short_raises(monkeypatch):
    """The same guard on the path that actually serves large files. No
    chunk is missing here, so `VaultIncompleteDocument`'s existing check
    passes and only the length disagrees."""
    monkeypatch.setattr(vault, "_fetch_chunks",
                        lambda ids, db: {"c1": "ab", "c2": "cd"})
    assert vault.vault_assemble(
        doc(children=["c1", "c2"], size=4), path=PATH) == "abcd"
    with pytest.raises(vault.VaultIncompleteDocument):
        vault.vault_assemble(doc(children=["c1", "c2"], size=4000), path=PATH)


# -------------------------------------------------- the second reader

# Every test above calls `vault_assemble`, and that is the whole gap they
# missed: `vault_bulk_fetch` assembles a document too, with its own inline
# loop, and never called `vault_assemble` — so `_size_checked` guarded the
# single-document read and left the bulk read exactly as blind as before.
# What that leaves unguarded, named precisely, because the first draft of
# this comment guessed and was wrong: the journal page
# (`nova_sources.journal_markdown`), the reply lookup behind
# `journal_folder_best_effort`, and the seven vault MCP tools. The site's
# other pages — board, comments, digest, costs, retros — and
# `fetch_vault_context`, which builds every heartbeat's prompt, all read
# single documents through `vault_read_path` and were covered by #202
# already. Smaller than "every page", and still the path Edvard's journal
# is rendered through.
#
# The two readers keep different *policies* on purpose (see
# test_vault_refuses_partial_documents): the single read raises, the bulk
# read drops the file and says so, because one damaged document must not
# blank a listing of several hundred. Only the blindness is fixed.


def _bulk(docs):
    """`vault_bulk_fetch` over exactly `docs`, with no CouchDB."""
    import json
    import urllib.parse

    def fake_couch_req(method, path, body=None):
        if "_all_docs" in path and method == "GET":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            start = json.loads(query.get("startkey", ['""'])[0])
            end = json.loads(query.get("endkey", ['"\U0010FFFF"'])[0])
            return 200, {"rows": [{"id": i} for i in sorted(docs)
                                  if start <= i <= end]}
        if "_all_docs" in path and method == "POST":
            return 200, {"rows": [{"id": k, "doc": docs[k]}
                                  for k in body["keys"] if k in docs]}
        return 404, {}

    return fake_couch_req


PREFIX = "projects/sokrates/projects/nova/"
OTHER = PREFIX + "ideas.md"


def test_bulk_fetch_still_returns_a_document_that_matches_its_size(monkeypatch):
    """The control, and it is doing real work here: without it every
    assertion below would pass against a bulk fetch that had started
    dropping everything, which is the failure this guard could introduce."""
    monkeypatch.setattr(vault, "couch_req", _bulk({
        PATH: doc(data="hello", size=5),
    }))
    assert dict(vault.vault_bulk_fetch(PREFIX)) == {PATH: "hello"}


def test_the_blind_read_that_lost_the_boards_is_caught_in_bulk_too(monkeypatch):
    """The same document that `test_the_blind_read_that_lost_the_boards_now_raises`
    pins, arriving through the reader the site actually uses. Before this it
    came back as `""` — a 123KB board rendering as an empty file, with
    nothing anywhere saying a read had failed."""
    monkeypatch.setattr(vault, "couch_req", _bulk({PATH: doc(size=REAL_SIZE)}))
    monkeypatch.setattr(vault, "log", lambda m: None)
    fetched = vault.vault_bulk_fetch(PREFIX)
    assert PATH not in fetched
    assert fetched == {}


def test_the_short_file_drops_out_and_the_healthy_ones_do_not(monkeypatch):
    """The policy difference from `vault_assemble`, stated as itself: this
    reader degrades per-file. A raise here would take down the journal page
    over one damaged document, which is the trade the chunk-missing branch
    beside it already refused to make."""
    monkeypatch.setattr(vault, "couch_req", _bulk({
        PATH: doc(size=REAL_SIZE),
        OTHER: dict(doc(data="# Ideas\n", size=8), _id=OTHER, path=OTHER),
    }))
    monkeypatch.setattr(vault, "log", lambda m: None)
    fetched = vault.vault_bulk_fetch(PREFIX)
    assert dict(fetched) == {OTHER: "# Ideas\n"}


def test_bulk_fetch_says_out_loud_that_it_dropped_a_short_file(monkeypatch):
    """Dropping quietly is the same bug one layer along — a page that
    silently loses a file looks identical to one that never had it. The
    caller asks `.unreadable`; `cycle_health` already reads that channel."""
    lines = []
    monkeypatch.setattr(vault, "couch_req", _bulk({PATH: doc(size=REAL_SIZE)}))
    monkeypatch.setattr(vault, "log", lines.append)
    fetched = vault.vault_bulk_fetch(PREFIX)
    assert any(PATH in line and str(REAL_SIZE) in line for line in lines)
    assert any(PATH in note and str(REAL_SIZE) in note
               for note in fetched.unreadable)


def test_a_chunked_document_that_assembles_short_drops_out_of_bulk(monkeypatch):
    """No chunk is missing, so the existing `VaultIncompleteDocument` check
    passes and only the length disagrees — the case the bulk loop had no way
    at all to notice, since it never consulted `size`."""
    monkeypatch.setattr(vault, "couch_req", _bulk({
        PATH: doc(children=["c1", "c2"], size=4000),
        "c1": {"_id": "c1", "data": "ab"},
        "c2": {"_id": "c2", "data": "cd"},
    }))
    monkeypatch.setattr(vault, "log", lambda m: None)
    assert vault.vault_bulk_fetch(PREFIX) == {}


def test_a_genuinely_empty_file_still_comes_back_from_bulk(monkeypatch):
    """The boundary. `size: 0` agrees with itself and is a real file — this
    is the assertion that stops the guard turning every empty note in the
    vault into a dropped one."""
    monkeypatch.setattr(vault, "couch_req", _bulk({PATH: doc(size=0)}))
    assert dict(vault.vault_bulk_fetch(PREFIX)) == {PATH: ""}


def test_a_document_with_no_size_field_still_comes_back_from_bulk(monkeypatch):
    """Three separate writers write to this vault. A bulk reader that
    required `size` would drop every file written by one that omits it."""
    monkeypatch.setattr(vault, "couch_req", _bulk({PATH: doc(data="hello")}))
    assert dict(vault.vault_bulk_fetch(PREFIX)) == {PATH: "hello"}


# -------------------------------------------------------------------- write


def test_an_ordinary_edit_is_allowed():
    """The control for the write half. Rolling one digest line, appending a
    capture, striking a bullet — none of these may need a flag, or the flag
    becomes the habit and the guard stops meaning anything."""
    assert _refusal(REAL_SIZE, REAL_SIZE - 200) is None
    assert _refusal(35859, 35400) is None


def test_the_write_that_lost_the_boards_is_refused():
    refusal = _refusal(REAL_SIZE, 0)
    assert refusal is not None
    assert refusal.startswith("FAILED(collapse:")
    assert str(REAL_SIZE) in refusal
    # The message has to say what to do. A caller reading it is mid-sequence
    # and its first instinct will be to retry the write.
    assert "allow_shrink" in refusal
    assert "re-reading" in refusal


def test_a_short_but_non_empty_write_is_also_refused():
    """The version nobody would notice. A partially-assembled read gives a
    plausible small body, not an empty one, so a guard that only checked for
    emptiness would pass the quieter half of this failure."""
    assert _refusal(REAL_SIZE, 10_000) is not None


def test_allow_shrink_lets_a_deliberate_truncation_through():
    assert _refusal(REAL_SIZE, 0, allow_shrink=True) is None


def test_a_small_document_may_still_be_rewritten_whole():
    """Below the floor the small JSON ledgers live, where replacing most of
    the file is the normal operation and the blast radius is a few rows."""
    assert _refusal(vault.COLLAPSE_FLOOR - 1, 0) is None


def test_creating_a_file_that_does_not_exist_is_not_a_collapse():
    """Every journal entry is this write. If it needed a flag the guard
    would be in the way of the loop's most common write."""
    assert vault._collapse_refusal(PATH, None, 0, False) is None


def test_a_document_with_no_size_field_is_not_guessed_at():
    assert vault._collapse_refusal(PATH, {"_rev": "1-a"}, 0, False) is None
    assert vault._collapse_refusal(PATH, {"size": "123586"}, 0, False) is None


def test_the_ratio_boundary_is_inclusive_on_the_permitted_side():
    """Exactly at the ratio is allowed, just under it is not — stated
    because 'a quarter' is ambiguous and the next reader will assume the
    other one."""
    old = 40_000
    exact = int(old * vault.COLLAPSE_RATIO)
    assert _refusal(old, exact) is None
    assert _refusal(old, exact - 1) is not None


def test_the_two_clients_agree_on_both_numbers():
    """The drift check that matters. These constants are hand-copied into
    `bridge/vault_tool.py`, and a guard that is stricter on one side than
    the other is worse than either — a cycle would learn the loose one's
    behaviour and be surprised by the tight one."""
    import pathlib
    import re
    here = pathlib.Path(__file__).resolve().parents[1]
    bridge = here.parent / "agora-claude-bridge" / "bridge" / "vault_tool.py"
    if not bridge.exists():
        pytest.skip("the bridge checkout is not beside this one")
    text = bridge.read_text(encoding="utf-8")
    for name, ours in (("COLLAPSE_FLOOR", vault.COLLAPSE_FLOOR),
                       ("COLLAPSE_RATIO", vault.COLLAPSE_RATIO)):
        match = re.search(rf"^{name} = (\S+)$", text, re.M)
        assert match, f"{name} is not defined in the bridge's client"
        assert float(match.group(1)) == float(ours)


def _refusal(old, new, allow_shrink=False):
    return vault._collapse_refusal(PATH, {"size": old}, new, allow_shrink)


# ------------------------------------------------------------- write, wired


def test_vault_write_path_passes_the_flag_through(monkeypatch):
    """The seam between the public function and the guard. Without this the
    flag could be accepted at the top and dropped on the way down, which
    reads as a working override and is not one."""
    seen = {}
    monkeypatch.setattr(
        vault, "_vault_put_raw",
        lambda path, content, existing=None, if_rev=None, allow_shrink=False:
            seen.update(allow_shrink=allow_shrink) or "written")
    vault.vault_write_path(PATH, "x", allow_shrink=True)
    assert seen == {"allow_shrink": True}
    vault.vault_write_path(PATH, "x")
    assert seen == {"allow_shrink": False}


# ---------------------------------------------------------- the MCP tool

def test_the_mcp_tool_can_actually_pass_the_flag_it_is_told_to_pass(monkeypatch):
    """The refusal message names `allow_shrink`, and until this was wired
    the `vault_write` tool had no argument to carry it — so a persona read
    an instruction it could not follow, on the one path where the file is
    still recoverable. A dead-end override is worse than none.
    """
    from agora_runner import tools_dispatch

    seen = {}
    monkeypatch.setattr(tools_dispatch, "vault_write_path",
                        lambda path, content, if_rev=None, allow_shrink=False:
                            seen.update(allow_shrink=allow_shrink) or "written")
    monkeypatch.setattr(tools_dispatch, "_before_snapshot", lambda path: "")
    monkeypatch.setattr(tools_dispatch, "_audit_vault_write",
                        lambda *a, **k: None)

    def call(args):
        seen.clear()
        return tools_dispatch.execute_tool(
            "vault_write", args, persona={"name": "nova"}, conversation_id="c1")

    assert call({"path": PATH, "content": "x"}) == "written"
    assert seen == {"allow_shrink": False}
    assert call({"path": PATH, "content": "x", "allow_shrink": True}) == "written"
    assert seen == {"allow_shrink": True}


def test_the_tool_schema_offers_the_flag_and_says_what_it_is_for():
    """A parameter the dispatch accepts but the schema never advertises is
    invisible to the model that needs it. Both sides, or neither."""
    from agora_runner import tools_schemas

    tools = tools_schemas.client_tool_schemas({"vaultWrite": True})
    write = next(t for t in tools if t["name"] == "vault_write")
    props = write["input_schema"]["properties"]
    assert "allow_shrink" in props
    assert props["allow_shrink"]["type"] == "boolean"
    # Not required: every ordinary write must stay a two-argument call.
    assert "allow_shrink" not in write["input_schema"]["required"]
    # The description has to state the refusal, or the model meets it for
    # the first time as an error it has no reason to expect.
    assert "allow_shrink" in write["description"]


def test_a_string_false_does_not_disable_the_guard():
    """A safety check must not fail open on a value it did not expect. A
    provider that serialises the argument loosely sends the string
    "false", and `bool("false")` is True — which would silently switch off
    the one guard this whole change exists to add, for a caller that
    explicitly declined it."""
    from agora_runner.tools_dispatch import _wants_shrink

    for no in (None, False, "", "false", "False", "FALSE", "no", "0", 0):
        assert _wants_shrink({"allow_shrink": no}) is False, no
    for yes in (True, "true", "True", "yes", "1", 1):
        assert _wants_shrink({"allow_shrink": yes}) is True, yes
    assert _wants_shrink({}) is False


def test_scoped_write_has_the_same_override_as_vault_write():
    """`scoped_write` shares `_conditional_write`, and therefore shares the
    refusal message that names `allow_shrink`. A sibling tool that inherits
    the refusal and not the flag tells its caller to do something it cannot
    do — the same dead end this cycle fixed for `vault_write`."""
    from agora_runner import tools_dispatch, tools_schemas

    step = {"filepath": "notes.md", "toolWhitelist": ["scoped_write"]}
    write = next(t for t in tools_schemas.client_tool_schemas({}, step)
                 if t["name"] == "scoped_write")
    assert "allow_shrink" in write["input_schema"]["properties"]
    assert "allow_shrink" not in write["input_schema"]["required"]
    assert "allow_shrink" in write["description"]

    seen = {}
    import unittest.mock as mock
    with mock.patch.object(tools_dispatch, "vault_write_path",
                           lambda path, content, if_rev=None, allow_shrink=False:
                               seen.update(allow_shrink=allow_shrink) or "written"), \
         mock.patch.object(tools_dispatch, "_before_snapshot", lambda p: ""), \
         mock.patch.object(tools_dispatch, "_audit_vault_write", lambda *a, **k: None):
        tools_dispatch.execute_tool(
            "scoped_write", {"content": "x", "allow_shrink": True},
            {"name": "P"}, "c1", step)
    assert seen == {"allow_shrink": True}


def test_both_clients_tell_the_caller_the_same_flag_spelling():
    """The refusal and the schema description are read back to back by the
    same model. Two spellings of one flag is a small thing that costs a
    retry."""
    from agora_runner import tools_schemas

    refusal = vault._collapse_refusal(PATH, {"size": REAL_SIZE}, 0, False)
    write = next(t for t in tools_schemas.client_tool_schemas({"vaultWrite": True})
                 if t["name"] == "vault_write")
    assert "allow_shrink=true" in refusal
    assert "allow_shrink=true" in write["description"]
