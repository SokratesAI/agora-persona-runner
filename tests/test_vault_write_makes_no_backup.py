"""A vault write tool may not promise a backup it does not make.

Why this exists (2026-08-07): #47 stopped `vault_write_path` snapshotting
the previous content into `agora/backups/` before every overwrite, and
deleted the folder. The implementation changed; the tool *descriptions*
handed to the model did not. For a day every persona with `vaultWrite`
was told "The previous version is automatically backed up to
agora/backups/ first", and `vault_update_frontmatter_batch` was told it
had the "Same automatic per-file backup as vault_write".

That is the same class of bug as #49 -- two things built from one dict,
free to disagree -- but it fails in the more expensive direction. #49's
drift promised tools that did not exist, which a model discovers the
moment it calls one. This one tells a model that clobbering a file is
free, and the model finds out it was wrong only after the content is
gone. `vault.py`'s own docstring says the quiet part: since 2026-08-06
"the only fallback is the *daily* GitHub snapshot, so a clobber-and-
restore now loses up to a day rather than nothing. That makes
[vault_append] the real protection, not a convenience." A description
that says otherwise argues the model out of the one habit that protects
the vault.

The two tests below are deliberately a pair. The first establishes the
ground truth by observation -- writing a file PUTs the file, and nothing
else -- so the second is not an arbitrary rule about wording but the
same fact, asserted where the model actually reads it.
"""
import re

import pytest

from agora_runner import vault
from agora_runner.tools_schemas import client_tool_schemas

# The tools that destroy existing content. Read tools legitimately mention
# the daily GitHub "backup mirror" (vault_git_revision_history and friends
# are built on it), and that reference is accurate -- the claim under test
# here is specifically the one made by a tool that can overwrite.
DESTRUCTIVE_VAULT_TOOLS = (
    "vault_write",
    "vault_append",
    "vault_update_frontmatter_batch",
)

BACKUP_CLAIM = re.compile(r"back(?:ed|s)?[ -]?up|backup", re.IGNORECASE)
NEGATED = re.compile(r"\bno\b|\bnot\b|\bnever\b", re.IGNORECASE)


def _descriptions():
    caps = {"vaultRead": True, "vaultWrite": True}
    return {t["name"]: t["description"] for t in client_tool_schemas(caps)}


def test_overwriting_a_file_creates_no_backup_document(monkeypatch):
    """Ground truth: one overwrite PUTs the chunk and the file, nothing more.

    `couch_req` is the single place vault.py persists anything -- even
    `couch_get_doc` goes through it -- so recording it captures every
    document that a write would create.

    The file has to already EXIST for this test to mean anything. The
    deleted backup step ran only when `couch_get_doc` came back 200, so a
    fake that 404s everything exercises the create path and passes against
    a version that still takes backups -- which is exactly what the first
    draft of this test did.
    """
    puts = []
    existing_doc = {"_id": "notes/thing.md", "_rev": "3-abc", "ctime": 1, "children": []}

    def fake_couch_req(method, path, body=None):
        if method == "PUT":
            puts.append((path, body))
            return 201, {"ok": True}
        if "notes%2Fthing.md" in path or "notes/thing.md" in path:
            return 200, existing_doc
        return 404, {}

    monkeypatch.setattr(vault, "couch_req", fake_couch_req)

    assert vault.vault_write_path("notes/thing.md", "replacement text") == "written"

    written_ids = [body["_id"] for _, body in puts]
    assert not [i for i in written_ids if i.startswith("agora/backups")], (
        f"a write created a backup document: {written_ids}"
    )
    # The chunk (content-addressed) and the file doc itself. A third PUT
    # would mean a copy of the old version went somewhere.
    assert len(puts) == 2, f"expected 2 PUTs (chunk + file), got {len(puts)}: {written_ids}"
    assert "notes/thing.md" in written_ids


@pytest.mark.parametrize("tool_name", DESTRUCTIVE_VAULT_TOOLS)
def test_destructive_vault_tool_does_not_promise_a_backup(tool_name):
    """No sentence may assert a backup happens, and none may name the
    deleted folder. Saying there is *no* backup is the point, so the rule
    is about unnegated claims, not about the word."""
    description = _descriptions()[tool_name]

    assert "agora/backups" not in description, (
        f"{tool_name} still names agora/backups/, deleted in #47"
    )

    for sentence in re.split(r"(?<=[.;])\s+|--", description):
        if BACKUP_CLAIM.search(sentence) and not NEGATED.search(sentence):
            pytest.fail(
                f"{tool_name} claims a backup it does not make: {sentence.strip()!r}"
            )
