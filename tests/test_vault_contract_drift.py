"""The two vault clients agree on the part of themselves that must agree.

`tools/vault_contract.py` is the comparison; this is the test that runs it
against the runner's real `agora_runner/vault.py`, plus the checks on the
comparison itself. The bridge's copy is in another repository, so the
cross-repo half runs in CI, which checks both out -- see the `vault-drift`
job in `.github/workflows/build.yaml`. What is testable here is that the
comparison notices what it claims to notice, because a drift check that
reports "in sync" whatever it is handed is the self-referential guard this
journal keeps rediscovering: `rolling.verify` splitting both sides with the
splitter under test, a linter piped through `tail`, browser doubles built
from the assumption they were testing, and a mutation harness that mutated
less than it said.

So every test below that expects silence is paired with one that expects
noise, over the same input.
"""
import ast

import pytest

from agora_runner import vault
from tools import vault_contract

RUNNER_SOURCE = open(vault.__file__, encoding="utf-8").read()

# A whole vault client is too big to inline, and a stub that only defines the
# contract names is exactly the double that would pass whatever it was given.
# So: the real runner source, edited. Each mutation below is a change a cycle
# could plausibly make to one copy and forget in the other.
def _mutated(old, new, source=None):
    source = RUNNER_SOURCE if source is None else source
    assert old in source, "mutation target %r is gone; fix this test" % (old,)
    return source.replace(old, new, 1)


def test_the_runner_contract_extracts_every_name_the_contract_lists():
    contract = vault_contract.extract_contract(RUNNER_SOURCE)
    expected = {n for n, _ in vault_contract.CONTRACT_CONSTANTS}
    expected |= {n for n, _ in vault_contract.CONTRACT_FUNCTIONS}
    assert set(contract) == expected
    assert all(v for v in contract.values())


def test_a_missing_name_is_an_error_and_names_all_of_them_at_once():
    stripped = _mutated("CHUNK_MIN_BYTES = 2048", "CHUNK_MIN_BYTES_X = 2048")
    stripped = _mutated("def _split_chunks(content)", "def _split_chunks_x(content)",
                        stripped)
    with pytest.raises(vault_contract.ContractNameMissing) as caught:
        vault_contract.extract_contract(stripped)
    assert "CHUNK_MIN_BYTES" in str(caught.value)
    assert "_split_chunks" in str(caught.value)


def test_the_two_live_clients_are_compared_by_value_not_by_text():
    """The runner source against itself, reflowed. Zero drift is the point,
    but only alongside the tests below that make it report drift -- on its
    own this passes for a comparison that always returns nothing."""
    reflowed = _mutated(
        'NOVA_DB_FILES = (\n    "projects/sokrates/projects/agora/journal-digest.md",\n)',
        'NOVA_DB_FILES = ("projects/sokrates/projects/agora/journal-digest.md",)')
    assert vault_contract.compare(RUNNER_SOURCE, reflowed) == []


def test_a_changed_routing_folder_is_drift():
    """The failure this exists for: one copy sends a path to Nova's database
    and the other to Edvard's, so his file is answered by the wrong store."""
    other = _mutated('"projects/sokrates/projects/agora/nova/",',
                     '"projects/sokrates/projects/nova/",')
    drifted = vault_contract.compare(RUNNER_SOURCE, other)
    assert "NOVA_DB_FOLDERS" in drifted
    # The derived tuple is followed through rather than compared as the
    # expression `NOVA_DB_FOLDERS + NOVA_DB_FILES`, which is textually equal
    # in both copies however far its operands have moved.
    assert "NOVA_DB_TARGETS" in drifted


def test_a_changed_health_probe_path_is_drift():
    other = _mutated('"projects/sokrates/projects/nova/issues.md",',
                     '"projects/sokrates/projects/agora/issues.md",')
    assert vault_contract.compare(RUNNER_SOURCE, other) == ["HEALTH_PROBE_PATHS"]


def test_a_changed_chunk_size_is_drift():
    other = _mutated("CHUNK_MAX_BYTES = 16384", "CHUNK_MAX_BYTES = 32768")
    assert vault_contract.compare(RUNNER_SOURCE, other) == ["CHUNK_MAX_BYTES"]


def test_a_changed_function_body_is_drift():
    """`_appended` decides where a capture lands relative to its marker --
    the split that took Cycles 112-114 to repair. A one-token change to it
    has to be visible."""
    other = _mutated("def _appended(existing_content, content, after_marker):",
                     "def _appended(existing_content, content, after_marker=''):")
    assert vault_contract.compare(RUNNER_SOURCE, other) == ["_appended"]


def test_a_reworded_comment_or_docstring_is_not_drift():
    """The two copies explain themselves to different readers by design --
    the bridge's docstrings address a CLI user, the runner's a caller. If
    prose counted, the check would be red permanently and get deleted."""
    other = _mutated(
        "# Obsidian LiveSync's own bookkeeping docs",
        "# Wholly different words about the same tuple, added by one repo")
    assert vault_contract.compare(RUNNER_SOURCE, other) == []

    node = next(n for n in ast.parse(RUNNER_SOURCE).body
                if isinstance(n, ast.FunctionDef) and n.name == "_split_chunks")
    # The first line only: `get_docstring` dedents, so the whole string is
    # not a substring of the source it came from.
    opening = (ast.get_docstring(node) or "").splitlines()[0]
    assert opening, "test assumes _split_chunks is documented"
    redocumented = _mutated(opening, "A completely different explanation.")
    assert vault_contract.compare(RUNNER_SOURCE, redocumented) == []
