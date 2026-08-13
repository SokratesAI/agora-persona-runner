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
import importlib
import os
import sys
import os
import sys

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


def test_a_routing_tuple_turned_into_a_list_is_drift():
    """Same strings, different brackets. `db_for` hands these to
    `str.startswith`, which takes a tuple and raises TypeError on a list --
    so this is a crash in the other pod, not a style difference, and a
    value-only comparison would call it in sync."""
    other = _mutated(
        'NOVA_DB_FOLDERS = (\n    "projects/sokrates/projects/agora/nova/",\n)',
        'NOVA_DB_FOLDERS = [\n    "projects/sokrates/projects/agora/nova/",\n]')
    assert "NOVA_DB_FOLDERS" in vault_contract.compare(RUNNER_SOURCE, other)


def test_a_changed_health_probe_path_is_drift():
    other = _mutated('"projects/sokrates/projects/nova/issues.md",',
                     '"projects/sokrates/projects/agora/issues.md",')
    assert vault_contract.compare(RUNNER_SOURCE, other) == ["HEALTH_PROBE_PATHS"]


def test_a_changed_chunk_size_is_drift():
    other = _mutated("CHUNK_MAX_BYTES = 16384", "CHUNK_MAX_BYTES = 32768")
    assert vault_contract.compare(RUNNER_SOURCE, other) == ["CHUNK_MAX_BYTES"]


def test_a_changed_function_signature_is_drift():
    """`_appended` decides where a capture lands relative to its marker --
    the split that took Cycles 112-114 to repair. Giving `after_marker` a
    default in one copy silently changes what an unmarked append does
    there."""
    other = _mutated("def _appended(existing_content, content, after_marker):",
                     "def _appended(existing_content, content, after_marker=''):")
    assert vault_contract.compare(RUNNER_SOURCE, other) == ["_appended"]


def test_a_changed_function_body_is_drift():
    """The signature test above is not this test: a name that says `body`
    and mutates the parameter list is the second-commonest finding in this
    repo's review rubric, so both are here and each mutates what it says."""
    other = _mutated("            if line.strip() == after_marker.strip():",
                     "            if line.strip().startswith(after_marker.strip()):")
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


# --- routing behaviour ------------------------------------------------------
#
# The name comparison above cannot reach routing at all: `db_for` is a method
# on `VaultClient` in the bridge and a plain function in the runner, so no
# module-level AST pairing can name it. `compare_routing` drives both copies
# instead. Same discipline as above -- every test that expects silence is
# paired with one that expects noise over the same input.

BRIDGE_STUB = '''
import os

NOVA_DB_FOLDERS = ("projects/sokrates/projects/agora/nova/",)
NOVA_DB_FILES = ("projects/sokrates/projects/agora/journal-digest.md",)
NOVA_DB_TARGETS = NOVA_DB_FOLDERS + NOVA_DB_FILES


class VaultClient:
    def __init__(self):
        self.db = os.environ["CDB_DB"]
        self.nova_db = os.environ.get("CDB_NOVA_DB", "")

    def db_for(self, path):
        if not self.nova_db:
            return self.db
        lowered = (path or "").lower()
        if lowered.startswith(NOVA_DB_FOLDERS) or lowered in NOVA_DB_FILES:
            return self.nova_db
        return self.db

    def dbs_for_prefix(self, prefix):
        if not self.nova_db:
            return [self.db]
        lowered = (prefix or "").lower()
        if lowered.startswith(NOVA_DB_FOLDERS):
            return [self.nova_db]
        if any(t.startswith(lowered) for t in NOVA_DB_TARGETS):
            return [self.db, self.nova_db]
        return [self.db]
'''


def _bridge_copy(tmp_path, source=None, old=None, new=None):
    """A second copy of the routing rule on disk, optionally mutated.

    A stub rather than the real bridge file, which lives in another
    repository and is not present in this test run -- the cross-repo
    comparison is CI's job (the `vault-drift` job checks both out). What is
    testable here is that `compare_routing` reports a difference when there
    is one and silence when there is not, and for that the stub has to be a
    faithful copy of the rule: it is written above to match
    `agora_runner/vault.py`, and the first test below is what keeps it
    honest -- if the runner's routing changes and the stub does not, that
    test goes red rather than the check quietly comparing something else.
    """
    source = BRIDGE_STUB if source is None else source
    if old is not None:
        assert old in source, "mutation target %r is gone; fix this test" % (old,)
        source = source.replace(old, new, 1)
    path = tmp_path / "vault_tool.py"
    path.write_text(source, encoding="utf-8")
    return str(path)


def test_the_two_copies_route_every_probed_path_the_same_way(tmp_path):
    assert vault_contract.compare_routing(
        vault.__file__, _bridge_copy(tmp_path)) == []


def test_the_exact_gap_the_name_comparison_could_not_see(tmp_path):
    """Delete the exact-file branch from one copy's `db_for` and the name
    comparison still says every name is in sync, because the tuple it reads
    is untouched -- that is what the second reader proved on #152.
    `journal-digest.md` is the file Edvard actually opens."""
    other = _bridge_copy(
        tmp_path,
        old="if lowered.startswith(NOVA_DB_FOLDERS) or lowered in NOVA_DB_FILES:",
        new="if lowered.startswith(NOVA_DB_FOLDERS):")
    drifted = vault_contract.compare_routing(vault.__file__, other)
    questions = [q for q, _, _ in drifted]
    assert questions == [
        "routing on: db_for('projects/sokrates/projects/agora/journal-digest.md')"]
    _, runner_answer, bridge_answer = drifted[0]
    assert runner_answer == vault_contract.PROBE_NOVA_DB
    assert bridge_answer == vault_contract.PROBE_DB


def test_a_prefix_that_straddles_both_databases_is_drift(tmp_path):
    """The ancestor branch: a listing of `projects/` has to query both
    databases or it quietly loses every file Nova owns."""
    other = _bridge_copy(
        tmp_path,
        old="""        if any(t.startswith(lowered) for t in NOVA_DB_TARGETS):
            return [self.db, self.nova_db]
""",
        new="")
    questions = [q for q, _, _ in vault_contract.compare_routing(vault.__file__, other)]
    assert "routing on: dbs_for_prefix('projects/')" in questions
    assert "routing on: dbs_for_prefix('')" in questions


def test_dropping_the_lowercase_is_drift(tmp_path):
    """A LiveSync document id is the lowercased path, so a copy that stopped
    lowercasing routes every capitalised path to the wrong database."""
    other = _bridge_copy(tmp_path,
                         old='lowered = (path or "").lower()',
                         new='lowered = (path or "")')
    questions = [q for q, _, _ in vault_contract.compare_routing(vault.__file__, other)]
    assert questions == [
        "routing on: db_for('PROJECTS/Sokrates/Projects/Agora/NOVA/"
        "journal/191-cycle-169.md')"]


def test_the_routing_off_configuration_is_compared_too(tmp_path):
    """`if not nova_db: return db` is a separately written early return in
    both copies and is the whole behaviour before the migration. A copy that
    lost it is drift no path in the routing-on pass can show, because with
    routing on the branch never runs."""
    other = _bridge_copy(
        tmp_path,
        old="""        if not self.nova_db:
            return self.db
        lowered = (path or "").lower()""",
        new='        lowered = (path or "").lower()')
    questions = [q for q, _, _ in vault_contract.compare_routing(vault.__file__, other)]
    assert questions, "the routing-off pass reported nothing"
    assert all(q.startswith("routing off:") for q in questions), questions


def test_a_copy_with_no_router_is_an_error_not_agreement(tmp_path):
    """Same reasoning as `ContractNameMissing`: a router that cannot be
    found is not a routing difference, and returning `[]` here would let a
    renamed function read as two copies agreeing."""
    other = _bridge_copy(tmp_path, old="    def db_for(self, path):",
                         new="    def db_for_path(self, path):")
    with pytest.raises(vault_contract.ContractRouterMissing):
        vault_contract.compare_routing(vault.__file__, other)


def test_two_copies_agreeing_on_a_database_neither_was_given_is_an_error():
    """The one way this comparison can be wrong by luck, and the reason it
    is checked at all: if both copies ignored the configuration -- reading
    the environment at call time, say -- every question would match and the
    run would report a comparison it never made. Driven directly, because
    reproducing it through two files means breaking both of them the same
    way, which is the assumption under test."""
    agreed = {"db_for('x')": "obsidian"}
    with pytest.raises(vault_contract.ContractRouterMissing) as caught:
        vault_contract._check_the_probe_reached_both(
            agreed, vault_contract.PROBE_DB, vault_contract.PROBE_NOVA_DB)
    assert "obsidian" in str(caught.value)

    # And it stays quiet on an answer that was configured, or the test above
    # only shows that it raises on everything.
    vault_contract._check_the_probe_reached_both(
        {"db_for('x')": vault_contract.PROBE_NOVA_DB},
        vault_contract.PROBE_DB, vault_contract.PROBE_NOVA_DB)


def test_a_difference_is_reported_as_drift_rather_than_as_incomparable(tmp_path):
    """A copy that routes somewhere neither database name covers is a
    finding, not a refusal. The guard above only covers agreement, because
    when the two answers differ there is nothing luck could have hidden --
    and reporting that as 'not comparable' would bury the useful half."""
    other = _bridge_copy(tmp_path,
                         old="        return self.nova_db",
                         new='        return "somewhere-else"')
    drifted = vault_contract.compare_routing(vault.__file__, other)
    assert any(a == "somewhere-else" or a == ["somewhere-else"]
               for _, _, a in drifted), drifted


@pytest.fixture
def restored_config():
    """Puts `agora_runner.config` and the probe env back after the test.

    The tests below deliberately leave a sentinel in both, so they have to
    clean up after themselves or they are the pollution they are checking
    for.
    """
    from agora_runner import config

    saved = {k: os.environ.get(k) for k in vault_contract._PROBE_ENV}
    try:
        yield config
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(config)


SENTINEL_DB = "sentinel-not-a-probe-db"


def test_the_comparison_puts_the_process_back_how_it_found_it(
        tmp_path, restored_config):
    """Driving two clients means setting the environment they read and
    reloading `agora_runner.config`, which is process-global state a dozen
    other tests import. Measured before the restore existed:
    `config.COUCHDB_DB` was left reading `probe-edvard-db` for the rest of
    the interpreter, and the suite stayed green only because no test that
    depends on it happened to run afterwards.

    The sentinel is the point. The first version of this test snapshotted
    the environment immediately before the call and compared it after --
    which passes with the restore deleted, because an earlier test in the
    same session has already polluted it and the snapshot is of the
    polluted value. It compared the bug to itself. A literal the
    comparison cannot move is the only assertion that survives.
    """
    os.environ["COUCHDB_DB"] = SENTINEL_DB
    os.environ.pop("COUCHDB_NOVA_DB", None)
    importlib.reload(restored_config)
    assert restored_config.COUCHDB_DB == SENTINEL_DB, "the sentinel never took"

    vault_contract.compare_routing(vault.__file__, _bridge_copy(tmp_path))

    assert os.environ["COUCHDB_DB"] == SENTINEL_DB
    assert "COUCHDB_NOVA_DB" not in os.environ
    assert restored_config.COUCHDB_DB == SENTINEL_DB
    assert restored_config.COUCHDB_NOVA_DB == ""
    assert not set(vault_contract._PROBE_MODULES) & set(sys.modules)


def test_the_process_is_put_back_even_when_the_comparison_raises(
        tmp_path, restored_config):
    """The restore is in a `finally` and this is what says so. A cleanup
    that only runs on the happy path is absent exactly when the run that
    needed it went wrong."""
    os.environ["COUCHDB_DB"] = SENTINEL_DB
    importlib.reload(restored_config)

    broken = _bridge_copy(tmp_path, old="    def db_for(self, path):",
                          new="    def db_for_path(self, path):")
    with pytest.raises(vault_contract.ContractRouterMissing):
        vault_contract.compare_routing(vault.__file__, broken)

    assert os.environ["COUCHDB_DB"] == SENTINEL_DB
    assert restored_config.COUCHDB_DB == SENTINEL_DB


def _runner_copy(tmp_path, old, new):
    """A mutated copy of the runner client, importable from tmp_path.

    Needed only by the test below, which requires *both* copies wrong the
    same way -- a guard that fires on agreement cannot be reached by
    breaking one side, because the other side keeps answering correctly and
    the pair is recorded as drift instead.
    """
    source = open(vault.__file__, encoding="utf-8").read()
    assert old in source, "mutation target %r is gone; fix this test" % (old,)
    path = tmp_path / "runner_vault.py"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return str(path)


# A path both copies will be made to answer with an unconfigured database.
# `db_for` lowercases, so this is what the id would really be.
_STRAY = "projects/sokrates/projects/nova/notes.md"

# The same early return in each copy's own indentation: a module-level
# function in the runner, a method in the bridge. That difference is the
# whole reason `compare_routing` exists, so the fixtures have to carry it.
_STRAY_RUNNER = '    if lowered == "%s":\n        return "obsidian"\n' % _STRAY
_STRAY_BRIDGE = '        if lowered == "%s":\n            return "obsidian"\n' % _STRAY


def test_real_drift_is_not_masked_by_the_probe_guard(tmp_path):
    """Second reader on #153. The guard used to raise inside the
    configuration loop, so a run that found real routing drift under one
    configuration and tripped the guard under the next reported an
    instrumentation error and discarded the routing bug it had already
    found -- a finding replaced by its own safety net.

    Reaching that needs both copies broken: the guard fires on *agreement*
    on a database neither was given, so breaking one side alone is recorded
    as drift and never reaches it. Both copies here answer `obsidian` for
    one path whatever they are configured with -- the real shape of a copy
    reading its own environment -- and the bridge additionally loses the
    exact-file branch, which is genuine drift on a different path.
    """
    runner = _runner_copy(
        tmp_path,
        old='    lowered = (path or "").lower()\n',
        new='    lowered = (path or "").lower()\n' + _STRAY_RUNNER)
    bridge = _bridge_copy(
        tmp_path,
        source=BRIDGE_STUB.replace(
            '        lowered = (path or "").lower()\n',
            '        lowered = (path or "").lower()\n' + _STRAY_BRIDGE, 1),
        old="if lowered.startswith(NOVA_DB_FOLDERS) or lowered in NOVA_DB_FILES:",
        new="if lowered.startswith(NOVA_DB_FOLDERS):")

    drifted = vault_contract.compare_routing(runner, bridge)

    questions = [q for q, _, _ in drifted]
    assert questions == [
        "routing on: db_for('projects/sokrates/projects/agora/journal-digest.md')"
    ], questions


def test_the_guard_still_fires_when_there_is_no_drift_to_report(tmp_path):
    """The other half, or the test above only shows the guard was removed.
    Same two copies, without the bridge-side drift: now nothing differs,
    the agreement on `obsidian` is the only signal left, and it must
    raise."""
    runner = _runner_copy(
        tmp_path,
        old='    lowered = (path or "").lower()\n',
        new='    lowered = (path or "").lower()\n' + _STRAY_RUNNER)
    bridge = _bridge_copy(tmp_path, source=BRIDGE_STUB.replace(
        '        lowered = (path or "").lower()\n',
        '        lowered = (path or "").lower()\n' + _STRAY_BRIDGE, 1))

    with pytest.raises(vault_contract.ContractRouterMissing) as caught:
        vault_contract.compare_routing(runner, bridge)
    assert "obsidian" in str(caught.value)
