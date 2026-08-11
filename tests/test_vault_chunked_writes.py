"""Content-defined chunking on the vault write path (Cycle 117).

Before this, `_vault_put_raw` stored the whole file as one content-hashed
blob and deleted nothing, so every append left a complete dead copy
behind. Measured on the live vault 2026-08-11: 38.8MB of dead chunks
against 1.4MB of live Nova content, 27.6x.

This module is the runner's half. `bridge/vault_tool.py` in
agora-claude-bridge carries the same chunker against the same database,
and `test_chunking_matches_the_bridge_copy` is what notices if they drift.
"""
from unittest.mock import patch

from agora_runner import vault


def _realistic_capture_file(entries=400):
    """Shaped like nova/resources/issues.md: a heading, then bullets, with
    the newest inserted directly under the heading."""
    body = "".join(
        f"- 2026-08-{(i % 28) + 1:02d} (Cycle {i}) - capture number {i}, "
        f"long enough to look like the real thing rather than a token.\n"
        for i in range(entries)
    )
    return "# Issues\n\n## Entries\n\n" + body


def test_split_chunks_concatenates_back_to_the_original():
    """The whole contract: vault_assemble() joins children with no separator."""
    for content in ("", "short\n", _realistic_capture_file(),
                    "no trailing newline", "unicode: åæø \U0001f600\n" * 500):
        assert "".join(vault._split_chunks(content)) == content


def test_split_chunks_produces_many_chunks_in_the_livesync_size_band():
    chunks = vault._split_chunks(_realistic_capture_file())
    assert len(chunks) > 5, "a 40KB file stored as one blob is the bug"
    sizes = [len(c.encode("utf-8")) for c in chunks]
    # Only the last chunk may fall under the minimum -- it is the remainder.
    assert all(s >= vault.CHUNK_MIN_BYTES for s in sizes[:-1])
    assert all(s <= vault.CHUNK_MAX_BYTES for s in sizes)


def test_split_chunks_breaks_up_a_single_oversized_line():
    """cost-ledger.json is one JSON line republished whole every cycle."""
    content = '{"a": "' + ("x" * 100_000) + '"}'
    chunks = vault._split_chunks(content)
    assert "".join(chunks) == content
    assert len(chunks) > 5
    assert all(len(c.encode("utf-8")) <= vault.CHUNK_MAX_BYTES for c in chunks)


def test_split_chunks_is_stable_across_processes():
    """A per-process salt (the builtin hash()) would re-chunk every file on
    every run and reuse nothing, which looks exactly like working."""
    import subprocess
    import sys
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from agora_runner import vault;"
        "print(len(vault._split_chunks(''.join("
        "'line %d of a file that needs to be long enough to matter\\n' % i"
        " for i in range(2000)))))"
    )
    runs = {
        subprocess.run([sys.executable, "-c", script], capture_output=True,
                       text=True, cwd=".").stdout.strip()
        for _ in range(3)
    }
    assert len(runs) == 1 and runs != {""}, runs


def test_append_rewrites_only_the_changed_chunks():
    """The measurement that made this cycle: an append must not rewrite the
    part of the file that did not change."""
    original = _realistic_capture_file()
    texts = vault._split_chunks(original)
    chunk_ids = [vault._chunk_id_for(t.encode("utf-8")) for t in texts]
    assert len(chunk_ids) > 5

    calls = []
    existing_data = dict(zip(chunk_ids, texts))

    def fake_req(method, path, body=None):
        calls.append((method, path, body))
        if method == "POST" and path.endswith("/_all_docs"):
            # Every chunk of the current file is already in the database.
            return 200, {"rows": [{"key": k, "id": k, "value": {"rev": "1-x"}}
                                  for k in sorted(set(body["keys"]))]}
        if method == "GET":
            doc_id = path.split("/", 1)[1].replace("%3A", ":")
            if doc_id == "notes%2Fissues.md" or doc_id == "notes/issues.md":
                return 200, {"children": list(chunk_ids), "_rev": "1-abc", "ctime": 1}
            if doc_id in existing_data:
                return 200, {"data": existing_data[doc_id]}
            return 404, {"error": "not_found"}
        return 201, {"ok": True}

    with patch.object(vault, "couch_req", fake_req):
        result = vault.vault_append_path(
            "notes/issues.md", "- 2026-08-11 (Cycle 117) - a new capture.",
            "## Entries")
    assert result == "written"

    chunk_puts = [c for c in calls if c[0] == "PUT" and "/h%3A" in c[1]]
    # The insert lands under a heading at the very top, so the boundary
    # shifts for a chunk or two and then re-syncs. Anything close to the
    # full file means the chunker is not content-defined.
    assert len(chunk_puts) <= 3, (
        f"{len(chunk_puts)} of {len(chunk_ids)} chunks rewritten for a "
        "one-line append")


def test_put_raw_refuses_to_write_a_file_doc_when_a_chunk_fails():
    """A file doc pointing at a missing chunk is VaultIncompleteDocument --
    silent on read, splicing surviving neighbours together mid-word."""
    calls = []

    def fake_req(method, path, body=None):
        calls.append((method, path, body))
        if method == "POST" and path.endswith("/_all_docs"):
            return 200, {"rows": []}
        if method == "GET":
            return 404, {"error": "not_found"}
        return 500, {"error": "server_error"}

    with patch.object(vault, "couch_req", fake_req):
        result = vault.vault_write_path("notes/thing.md", _realistic_capture_file())

    assert result.startswith("FAILED(chunk ")
    assert [c for c in calls if c[0] == "PUT" and "notes%2Fthing.md" in c[1]] == []


def test_chunking_matches_the_bridge_copy():
    """The two clients write the same CouchDB. If they chunk differently
    they stop reusing each other's chunks and the write amplification comes
    straight back for whichever file they take turns writing. There is no
    import between the repos, so this pins the constants."""
    assert (vault.CHUNK_MIN_BYTES, vault.CHUNK_MAX_BYTES,
            vault.CHUNK_BOUNDARY_MASK) == (2048, 16384, 0x1F)
    assert vault._split_chunks("x\n" * 5000) == vault._split_chunks("x\n" * 5000)
