"""The two vault clients agree on the part of themselves that must agree.

`tools/sync_contract.py` is the comparison; this is the test that runs it
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
import re
import os
import sys
import time

import pytest

from agora_runner import vault

# `from agora_runner import redact` gets the function, not the module -- the
# package re-exports it. This wants the module, for its `__file__`.
redact = importlib.import_module("agora_runner.redact")
from tools import sync_contract

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
    contract = sync_contract.extract_contract(RUNNER_SOURCE)
    expected = {n for n, _ in sync_contract.CONTRACT_CONSTANTS}
    expected |= {n for n, _ in sync_contract.CONTRACT_FUNCTIONS}
    assert set(contract) == expected
    assert all(v for v in contract.values())


def test_a_missing_name_is_an_error_and_names_all_of_them_at_once():
    stripped = _mutated("CHUNK_MIN_BYTES = 2048", "CHUNK_MIN_BYTES_X = 2048")
    stripped = _mutated("def _split_chunks(content)", "def _split_chunks_x(content)",
                        stripped)
    with pytest.raises(sync_contract.ContractNameMissing) as caught:
        sync_contract.extract_contract(stripped)
    assert "CHUNK_MIN_BYTES" in str(caught.value)
    assert "_split_chunks" in str(caught.value)


def test_the_two_live_clients_are_compared_by_value_not_by_text():
    """The runner source against itself, reflowed. Zero drift is the point,
    but only alongside the tests below that make it report drift -- on its
    own this passes for a comparison that always returns nothing."""
    reflowed = _mutated(
        'NOVA_DB_FILES = (\n    "projects/sokrates/projects/agora/journal-digest.md",\n)',
        'NOVA_DB_FILES = ("projects/sokrates/projects/agora/journal-digest.md",)')
    assert sync_contract.compare(RUNNER_SOURCE, reflowed) == []


def test_a_changed_routing_folder_is_drift():
    """The failure this exists for: one copy sends a path to Nova's database
    and the other to Edvard's, so his file is answered by the wrong store."""
    other = _mutated('"projects/sokrates/projects/agora/nova/",',
                     '"projects/sokrates/projects/nova/",')
    drifted = sync_contract.compare(RUNNER_SOURCE, other)
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
    assert "NOVA_DB_FOLDERS" in sync_contract.compare(RUNNER_SOURCE, other)


def test_a_changed_health_probe_path_is_drift():
    other = _mutated('"projects/sokrates/projects/nova/issues.md",',
                     '"projects/sokrates/projects/agora/issues.md",')
    assert sync_contract.compare(RUNNER_SOURCE, other) == ["HEALTH_PROBE_PATHS"]


def test_a_changed_chunk_size_is_drift():
    other = _mutated("CHUNK_MAX_BYTES = 16384", "CHUNK_MAX_BYTES = 32768")
    assert sync_contract.compare(RUNNER_SOURCE, other) == ["CHUNK_MAX_BYTES"]


def test_a_changed_function_signature_is_drift():
    """`_appended` decides where a capture lands relative to its marker --
    the split that took Cycles 112-114 to repair. Giving `after_marker` a
    default in one copy silently changes what an unmarked append does
    there."""
    other = _mutated("def _appended(existing_content, content, after_marker):",
                     "def _appended(existing_content, content, after_marker=''):")
    assert sync_contract.compare(RUNNER_SOURCE, other) == ["_appended"]


def test_a_changed_function_body_is_drift():
    """The signature test above is not this test: a name that says `body`
    and mutates the parameter list is the second-commonest finding in this
    repo's review rubric, so both are here and each mutates what it says."""
    other = _mutated("            if line.strip() == after_marker.strip():",
                     "            if line.strip().startswith(after_marker.strip()):")
    assert sync_contract.compare(RUNNER_SOURCE, other) == ["_appended"]


def test_a_reworded_comment_or_docstring_is_not_drift():
    """The two copies explain themselves to different readers by design --
    the bridge's docstrings address a CLI user, the runner's a caller. If
    prose counted, the check would be red permanently and get deleted."""
    other = _mutated(
        "# Obsidian LiveSync's own bookkeeping docs",
        "# Wholly different words about the same tuple, added by one repo")
    assert sync_contract.compare(RUNNER_SOURCE, other) == []

    node = next(n for n in ast.parse(RUNNER_SOURCE).body
                if isinstance(n, ast.FunctionDef) and n.name == "_split_chunks")
    # The first line only: `get_docstring` dedents, so the whole string is
    # not a substring of the source it came from.
    opening = (ast.get_docstring(node) or "").splitlines()[0]
    assert opening, "test assumes _split_chunks is documented"
    redocumented = _mutated(opening, "A completely different explanation.")
    assert sync_contract.compare(RUNNER_SOURCE, redocumented) == []


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
    assert sync_contract.compare_routing(
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
    drifted = sync_contract.compare_routing(vault.__file__, other)
    questions = [q for q, _, _ in drifted]
    assert questions == [
        "routing on: db_for('projects/sokrates/projects/agora/journal-digest.md')"]
    _, runner_answer, bridge_answer = drifted[0]
    assert runner_answer == sync_contract.PROBE_NOVA_DB
    assert bridge_answer == sync_contract.PROBE_DB


def test_a_prefix_that_straddles_both_databases_is_drift(tmp_path):
    """The ancestor branch: a listing of `projects/` has to query both
    databases or it quietly loses every file Nova owns."""
    other = _bridge_copy(
        tmp_path,
        old="""        if any(t.startswith(lowered) for t in NOVA_DB_TARGETS):
            return [self.db, self.nova_db]
""",
        new="")
    questions = [q for q, _, _ in sync_contract.compare_routing(vault.__file__, other)]
    assert "routing on: dbs_for_prefix('projects/')" in questions
    assert "routing on: dbs_for_prefix('')" in questions


def test_dropping_the_lowercase_is_drift(tmp_path):
    """A LiveSync document id is the lowercased path, so a copy that stopped
    lowercasing routes every capitalised path to the wrong database."""
    other = _bridge_copy(tmp_path,
                         old='lowered = (path or "").lower()',
                         new='lowered = (path or "")')
    questions = [q for q, _, _ in sync_contract.compare_routing(vault.__file__, other)]
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
    questions = [q for q, _, _ in sync_contract.compare_routing(vault.__file__, other)]
    assert questions, "the routing-off pass reported nothing"
    assert all(q.startswith("routing off:") for q in questions), questions


def test_a_copy_with_no_router_is_an_error_not_agreement(tmp_path):
    """Same reasoning as `ContractNameMissing`: a router that cannot be
    found is not a routing difference, and returning `[]` here would let a
    renamed function read as two copies agreeing."""
    other = _bridge_copy(tmp_path, old="    def db_for(self, path):",
                         new="    def db_for_path(self, path):")
    with pytest.raises(sync_contract.ContractRouterMissing):
        sync_contract.compare_routing(vault.__file__, other)


def test_two_copies_agreeing_on_a_database_neither_was_given_is_an_error():
    """The one way this comparison can be wrong by luck, and the reason it
    is checked at all: if both copies ignored the configuration -- reading
    the environment at call time, say -- every question would match and the
    run would report a comparison it never made. Driven directly, because
    reproducing it through two files means breaking both of them the same
    way, which is the assumption under test."""
    agreed = {"db_for('x')": "obsidian"}
    with pytest.raises(sync_contract.ContractRouterMissing) as caught:
        sync_contract._check_the_probe_reached_both(
            agreed, sync_contract.PROBE_DB, sync_contract.PROBE_NOVA_DB)
    assert "obsidian" in str(caught.value)

    # And it stays quiet on an answer that was configured, or the test above
    # only shows that it raises on everything.
    sync_contract._check_the_probe_reached_both(
        {"db_for('x')": sync_contract.PROBE_NOVA_DB},
        sync_contract.PROBE_DB, sync_contract.PROBE_NOVA_DB)


def test_a_difference_is_reported_as_drift_rather_than_as_incomparable(tmp_path):
    """A copy that routes somewhere neither database name covers is a
    finding, not a refusal. The guard above only covers agreement, because
    when the two answers differ there is nothing luck could have hidden --
    and reporting that as 'not comparable' would bury the useful half."""
    other = _bridge_copy(tmp_path,
                         old="        return self.nova_db",
                         new='        return "somewhere-else"')
    drifted = sync_contract.compare_routing(vault.__file__, other)
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

    saved = {k: os.environ.get(k) for k in sync_contract._PROBE_ENV}
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

    sync_contract.compare_routing(vault.__file__, _bridge_copy(tmp_path))

    assert os.environ["COUCHDB_DB"] == SENTINEL_DB
    assert "COUCHDB_NOVA_DB" not in os.environ
    assert restored_config.COUCHDB_DB == SENTINEL_DB
    assert restored_config.COUCHDB_NOVA_DB == ""
    assert not set(sync_contract._PROBE_MODULES) & set(sys.modules)


def test_the_process_is_put_back_even_when_the_comparison_raises(
        tmp_path, restored_config):
    """The restore is in a `finally` and this is what says so. A cleanup
    that only runs on the happy path is absent exactly when the run that
    needed it went wrong."""
    os.environ["COUCHDB_DB"] = SENTINEL_DB
    importlib.reload(restored_config)

    broken = _bridge_copy(tmp_path, old="    def db_for(self, path):",
                          new="    def db_for_path(self, path):")
    with pytest.raises(sync_contract.ContractRouterMissing):
        sync_contract.compare_routing(vault.__file__, broken)

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

    drifted = sync_contract.compare_routing(runner, bridge)

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

    with pytest.raises(sync_contract.ContractRouterMissing) as caught:
        sync_contract.compare_routing(runner, bridge)
    assert "obsidian" in str(caught.value)


# --- routing: the expected answer, not just agreement -----------------------
#
# Everything above this line asks whether the two copies agree, plus one guard
# against agreeing on a name neither was given. Both are blind to two copies
# edited the same way *within* the two databases they were given, which is the
# ordinary shape of this mistake: one hand-written fix typed into both files.
# Each test below therefore breaks both copies identically, and none of them
# can be reached by breaking one.
#
# The routing-off configuration is deliberately absent from this section: with
# `COUCHDB_NOVA_DB` empty there is exactly one configured database, so there is
# no wrong-but-configured answer to give. Every routing-off failure is either a
# difference between the copies or a stray name, and the two guards above
# already own both.


def _both_copies(tmp_path, runner_old, runner_new, bridge_old, bridge_new):
    """One mutation, written into each copy in its own spelling."""
    return (_runner_copy(tmp_path, old=runner_old, new=runner_new),
            _bridge_copy(tmp_path, old=bridge_old, new=bridge_new))


def _answers_agree(runner_path, bridge_path):
    """Do the two copies answer every routing question identically?

    `compare_routing`'s drive loop with the expected-answer check left off,
    so a test below can show that its mutation was caught by the expectations
    and not by the two copies disagreeing. Asserting that from
    `compare_routing` alone would be circular -- it runs the check only when
    nothing drifted, so the test would be resting on the behaviour it exists
    to pin.
    """
    with sync_contract._process_state_restored():
        for db, nova_db in sync_contract.ROUTING_CONFIGS:
            runner = sync_contract._runner_router(runner_path, db, nova_db)
            bridge = sync_contract._bridge_router(bridge_path, db, nova_db)
            paths = tuple(sys.modules["_sync_contract_runner"].HEALTH_PROBE_PATHS)
            paths += sync_contract.EXTRA_PROBE_PATHS
            if (sync_contract._routing_answers(*runner, paths)
                    != sync_contract._routing_answers(*bridge, paths)):
                return False
    return True


def test_edvards_own_nova_folder_added_to_both_copies_is_caught(tmp_path):
    """The mistake this table was written for, and it is a named trap rather
    than an invented one: two folders in the vault are called "nova" and only
    `agora/nova/` is Nova's. `projects/nova/` is Edvard's own, deliberately
    left in his vault so his capture files survive the app breaking.

    Adding it to `NOVA_DB_FOLDERS` routes his `issues.md`, `ideas.md` and
    `notes.md` into Nova's database, where his phone does not look. Typed
    into both copies -- which is how every fix in this pair gets written --
    the two agree, both answers are databases this tool configured, and every
    other guard on this stage is green.
    """
    runner, bridge = _both_copies(
        tmp_path,
        runner_old='NOVA_DB_FOLDERS = (\n    "projects/sokrates/projects/agora/nova/",\n)',
        runner_new='NOVA_DB_FOLDERS = (\n    "projects/sokrates/projects/agora/nova/",\n'
                   '    "projects/sokrates/projects/nova/",\n)',
        bridge_old='NOVA_DB_FOLDERS = ("projects/sokrates/projects/agora/nova/",)',
        bridge_new='NOVA_DB_FOLDERS = ("projects/sokrates/projects/agora/nova/",'
                   ' "projects/sokrates/projects/nova/",)')

    # Agreement first, or this test proves nothing about the expected table:
    # if the copies differed, drift would be reported and the check would
    # never run.
    assert _answers_agree(runner, bridge)

    with pytest.raises(sync_contract.ContractRouterMissing) as caught:
        sync_contract.compare_routing(runner, bridge)
    message = str(caught.value)
    assert "projects/sokrates/projects/nova/issues.md" in message
    assert "expected" in message


def test_the_exact_file_branch_lost_from_both_copies_is_caught(tmp_path):
    """`journal-digest.md` is the file Edvard opens, and it is the one file
    routed by exact match rather than by folder. Losing that branch in one
    copy is the drift `test_the_exact_gap_the_name_comparison_could_not_see`
    covers. Losing it in both is the same page reading from the wrong
    database in both processes, with nothing above this line able to say so.
    """
    runner, bridge = _both_copies(
        tmp_path,
        runner_old="if lowered.startswith(NOVA_DB_FOLDERS) or lowered in NOVA_DB_FILES:",
        runner_new="if lowered.startswith(NOVA_DB_FOLDERS):",
        bridge_old="if lowered.startswith(NOVA_DB_FOLDERS) or lowered in NOVA_DB_FILES:",
        bridge_new="if lowered.startswith(NOVA_DB_FOLDERS):")

    assert _answers_agree(runner, bridge)

    with pytest.raises(sync_contract.ContractRouterMissing) as caught:
        sync_contract.compare_routing(runner, bridge)
    assert "journal-digest.md" in str(caught.value)


def test_a_straddling_prefix_narrowed_in_both_copies_is_caught(tmp_path):
    """The ancestor branch, dropped from both. A listing of `projects/` then
    queries only Edvard's database and silently returns none of Nova's files
    -- and because both copies return `[probe-edvard-db]`, which is a
    configured name, agreement and the stray guard are both satisfied.
    """
    runner, bridge = _both_copies(
        tmp_path,
        runner_old="    if any(t.startswith(lowered) for t in NOVA_DB_TARGETS):\n"
                   "        return [COUCHDB_DB, COUCHDB_NOVA_DB]\n",
        runner_new="",
        bridge_old="        if any(t.startswith(lowered) for t in NOVA_DB_TARGETS):\n"
                   "            return [self.db, self.nova_db]\n",
        bridge_new="")

    assert _answers_agree(runner, bridge)

    with pytest.raises(sync_contract.ContractRouterMissing) as caught:
        sync_contract.compare_routing(runner, bridge)
    assert "dbs_for_prefix" in str(caught.value)


def test_the_expected_table_stays_quiet_on_the_copies_as_they_are(tmp_path):
    """The control for the three above. The live pair is checked by
    `test_the_two_copies_route_every_probed_path_the_same_way`, which would
    also go red if the expected table were wrong -- this asserts it directly
    so a failure names the expectation rather than the comparison.
    """
    paths = tuple(vault.HEALTH_PROBE_PATHS) + sync_contract.EXTRA_PROBE_PATHS
    for db, nova_db in sync_contract.ROUTING_CONFIGS:
        expected = sync_contract._routing_expectations(paths, db, nova_db)
        assert expected, "no expectations were built"
        sync_contract._check_the_probe_answered_correctly(
            expected, paths, db, nova_db)


def test_a_probed_path_with_no_expectation_stops_the_run():
    """`HEALTH_PROBE_PATHS` is read off the runner copy at run time, so a
    path added there arrives here with no expectation. Skipping it would
    make the newest probe -- the one somebody just decided was worth
    checking -- the only one compared by agreement alone.
    """
    paths = ("projects/sokrates/projects/agora/nova/journal/999-cycle-999.md",)
    with pytest.raises(sync_contract.ContractRouterMissing) as caught:
        sync_contract._routing_expectations(
            paths, sync_contract.PROBE_DB, sync_contract.PROBE_NOVA_DB)
    assert "999-cycle-999.md" in str(caught.value)


def test_an_expectation_nothing_probes_stops_the_run():
    """The mirror, and the reason it is not merely tidiness: the stage's
    success line counts this table, so a row that no longer corresponds to a
    probe makes the CI log claim a check that did not run. Under-claiming
    was worth fixing at #156; over-claiming is the same fault, pointed the
    unfriendly way.
    """
    paths = tuple(p for p in sync_contract._ROUTING_EXPECTED_PATHS
                  if p != "unrelated/file.md")
    with pytest.raises(sync_contract.ContractRouterMissing) as caught:
        sync_contract._routing_expectations(
            paths, sync_contract.PROBE_DB, sync_contract.PROBE_NOVA_DB)
    assert "unrelated/file.md" in str(caught.value)


def test_the_success_line_counts_every_probe_it_actually_ran(tmp_path):
    """The count in the CI log, against the questions the comparison really
    asked. `_routing_expectations` keys its dict exactly as `_routing_answers`
    does, so this also pins the two spellings of that format string together.
    """
    stage = [s for s in sync_contract._VAULT_STAGES
             if s.label == "vault routing"][0]
    summary = stage.summary(vault.__file__, _bridge_copy(tmp_path))

    paths = tuple(vault.HEALTH_PROBE_PATHS) + sync_contract.EXTRA_PROBE_PATHS
    asked = len(sync_contract._routing_expectations(
        paths, sync_contract.PROBE_DB, sync_contract.PROBE_NOVA_DB))
    assert summary.startswith(
        "%d " % (asked * len(sync_contract.ROUTING_CONFIGS),)), summary


# ---------------------------------------------------------------------------
# Chunk assembly: which database a document's chunks are fetched from. Same
# discipline again -- silence and noise over the same input -- with one extra
# obligation the routing tests do not carry. Every database in play here is
# one of the two configured, so an answer cannot be visibly stray: two copies
# that drifted the same way agree on real names. Hence the tests that expect
# an *error* on agreement.
# ---------------------------------------------------------------------------

# The bridge's `assemble`, as a method, appended to the routing stub's class.
# Faithful to `bridge/vault_tool.py` in the one line this comparison reads --
# how `db` is chosen -- and deliberately not in what it does with a missing
# chunk, because the probe answers every id and never reaches that branch.
BRIDGE_ASSEMBLE_STUB = BRIDGE_STUB + '''
    def _fetch_chunks(self, chunk_ids, db):
        raise AssertionError("the probe is meant to replace this")

    def assemble(self, doc, path=None, db=None):
        kids = doc.get("children") or []
        if not kids:
            return doc.get("data", "")
        db = db or doc.get(_SRC_DB_KEY) or self.db_for(
            path or doc.get("path") or doc.get("_id"))
        by_id = self._fetch_chunks(kids, db)
        return "".join(by_id.get(chunk_id, "") for chunk_id in kids)


_SRC_DB_KEY = "_nova_src_db"
'''

# The line each copy makes its one decision on, in each copy's own shape.
_PICK_RUNNER = "db = db or doc.get(_SRC_DB_KEY) or db_for("
_PICK_BRIDGE = "db = db or doc.get(_SRC_DB_KEY) or self.db_for("


def _assemble_pair(tmp_path, runner=(None, None), bridge=(None, None)):
    """`(runner path, bridge path)`, each optionally mutated once."""
    runner_path = str(tmp_path / "runner_vault.py")
    source = RUNNER_SOURCE
    if runner[0] is not None:
        source = _mutated(runner[0], runner[1])
    open(runner_path, "w", encoding="utf-8").write(source)
    return runner_path, _bridge_copy(
        tmp_path, source=BRIDGE_ASSEMBLE_STUB, old=bridge[0], new=bridge[1])


def test_the_two_copies_read_every_probed_document_from_the_same_database(
        tmp_path):
    assert sync_contract.compare_assembly(*_assemble_pair(tmp_path)) == []


def test_the_gap_this_pair_was_built_for(tmp_path):
    """Cycle 169's finding, which nothing prevented coming back.

    The runner recomputed the route instead of honouring the database the
    doc was actually read from. Both name comparison and routing comparison
    stay silent on it: the constant is still defined, `db_for` still answers
    every path identically, and the drift is one term of one expression
    inside a function no AST pairing can reach.
    """
    runner, bridge = _assemble_pair(
        tmp_path, runner=(_PICK_RUNNER, "db = db or db_for("))
    drifted = sync_contract.compare_assembly(runner, bridge)
    assert sorted(q for q, _, _ in drifted) == [
        "assembly, routing off: stamped doc, no path anywhere",
        "assembly, routing off: stamped doc, path routes the other way",
        "assembly, routing on: stamped doc, no path anywhere",
        "assembly, routing on: stamped doc, path routes the other way",
    ]
    # And the name comparison really is blind to it, which is the claim that
    # justifies this comparison existing at all.
    assert sync_contract.compare(
        RUNNER_SOURCE, _mutated(_PICK_RUNNER, "db = db or db_for(")) == []


def test_an_explicit_database_argument_dropped_on_one_side_is_drift(tmp_path):
    """`vault_bulk_fetch` passes the database it just read the doc out of.
    A copy that lets the stamp win over it is reading with an argument it
    was told to ignore."""
    runner, bridge = _assemble_pair(
        tmp_path,
        runner=(_PICK_RUNNER, "db = doc.get(_SRC_DB_KEY) or db or db_for("))
    drifted = sync_contract.compare_assembly(runner, bridge)
    assert sorted(q for q, _, _ in drifted) == [
        "assembly, routing off: explicit db beats the stamp",
        "assembly, routing on: explicit db beats the stamp",
    ]


def test_the_explicit_row_catches_a_dropped_argument_on_its_own(tmp_path):
    """A copy that ignores the `db=` argument *and* the stamp and routes by
    path. The second reader on #156 found this scored the explicit row for
    free, because that row's path used to route to the same database the
    argument named. Other rows caught it, which is why it was a near-miss
    and not a bug -- but a row that needs another row to mean anything is
    not the row it says it is."""
    runner, bridge = _assemble_pair(
        tmp_path, runner=(_PICK_RUNNER, "db = db_for("))
    drifted = [q for q, _, _ in sync_contract.compare_assembly(runner, bridge)]
    assert "assembly, routing on: explicit db beats the stamp" in drifted


def test_both_copies_dropping_the_stamp_is_an_error_not_agreement(tmp_path):
    """The one this table exists for, and the one routing's guard shape
    cannot catch: both copies answer with a database this comparison did
    configure, and they answer with the same one."""
    runner, bridge = _assemble_pair(
        tmp_path,
        runner=(_PICK_RUNNER, "db = db or db_for("),
        bridge=(_PICK_BRIDGE, "db = db or self.db_for("))
    with pytest.raises(sync_contract.ContractAssemblerMissing) as caught:
        sync_contract.compare_assembly(runner, bridge)
    assert "stamped doc, path routes the other way" in str(caught.value)


def test_the_routing_off_configuration_is_driven_too(tmp_path):
    """Both copies honour the stamp only while Nova's database is
    configured. Routing-on is identical to the real thing, so a comparison
    that ran one configuration would report this pair in sync."""
    runner, bridge = _assemble_pair(
        tmp_path,
        runner=(_PICK_RUNNER,
                "db = db or (doc.get(_SRC_DB_KEY) if COUCHDB_NOVA_DB else None)"
                " or db_for("),
        bridge=(_PICK_BRIDGE,
                "db = db or (doc.get(_SRC_DB_KEY) if self.nova_db else None)"
                " or self.db_for("))
    with pytest.raises(sync_contract.ContractAssemblerMissing) as caught:
        sync_contract.compare_assembly(runner, bridge)
    assert "'probe-edvard-db'" in str(caught.value)


def test_a_copy_with_no_assembler_is_an_error_not_agreement(tmp_path):
    """The routing stub, which has no `assemble` at all. Silence here would
    be a green CI job comparing nothing."""
    runner, _ = _assemble_pair(tmp_path)
    with pytest.raises(sync_contract.ContractAssemblerMissing) as caught:
        sync_contract.compare_assembly(runner, _bridge_copy(tmp_path))
    assert "assemble" in str(caught.value)


def test_real_assembly_drift_is_not_masked_by_the_guard(tmp_path):
    """Both copies wrong the same way on one row, and one of them wrong
    again on another. The named difference has to survive: replacing it
    with an instrumentation error is a finding masked by its own safety
    net, which is what the second reader caught on #153 for routing."""
    runner, bridge = _assemble_pair(
        tmp_path,
        runner=(_PICK_RUNNER, "db = db or db_for("),
        bridge=(_PICK_BRIDGE, "db = db or self.db or self.db_for("))
    drifted = sync_contract.compare_assembly(runner, bridge)
    # Both dropped the stamp, so the two stamped rows agree on a wrong
    # answer -- and the guard must not get to speak, because these did not.
    assert sorted(q for q, _, _ in drifted) == [
        "assembly, routing on: unstamped, path argument inside Nova's folder",
        "assembly, routing on: unstamped, path off the doc's `_id`",
        "assembly, routing on: unstamped, path off the doc's own `path`",
    ]


def test_the_assembly_comparison_puts_the_process_back_too(
        tmp_path, restored_config):
    """It configures both copies out of the environment exactly as
    `compare_routing` does, and loads two more modules. The restore is
    shared, so this asserts the shared restore actually covers them --
    `_PROBE_MODULES` is a list somebody has to remember to add to, and the
    routing test above cannot notice a name it never loads."""
    os.environ["COUCHDB_DB"] = SENTINEL_DB
    os.environ.pop("COUCHDB_NOVA_DB", None)
    importlib.reload(restored_config)
    assert restored_config.COUCHDB_DB == SENTINEL_DB, "the sentinel never took"

    sync_contract.compare_assembly(*_assemble_pair(tmp_path))

    assert os.environ["COUCHDB_DB"] == SENTINEL_DB
    assert "COUCHDB_NOVA_DB" not in os.environ
    assert restored_config.COUCHDB_DB == SENTINEL_DB
    assert "_sync_contract_runner_assemble" not in sys.modules
    assert "_sync_contract_bridge_assemble" not in sys.modules


def test_the_probe_table_would_notice_a_copy_that_hardcoded_one_database():
    """Every expectation token is used, and under routing-on they do not all
    resolve to the same name. A table whose rows all expect one database is
    satisfied by a copy that ignores its arguments entirely."""
    tokens = {row[4] for row in sync_contract._assembly_questions("_k")}
    assert tokens == set(sync_contract._ASSEMBLY_EXPECTED)
    wanted = {sync_contract._ASSEMBLY_EXPECTED[t](
        sync_contract.PROBE_DB, sync_contract.PROBE_NOVA_DB) for t in tokens}
    assert len(wanted) > 1


# ---------------------------------------------------------------------------
# The redaction pair. Same shape as above: every test expecting silence is
# paired with one expecting noise, over the same input.
# ---------------------------------------------------------------------------

REDACT_SOURCE = open(redact.__file__, encoding="utf-8").read()


def _redact_copy(tmp_path, old=None, new=None, name="redact_copy.py"):
    """A second copy of redact() on disk, optionally mutated.

    The real runner source rather than a stub, for the reason `_bridge_copy`
    gives: a stub that only defines `redact` is the double that agrees with
    whatever it is handed. The bridge's copy lives in another repository and
    is compared in CI.
    """
    source = REDACT_SOURCE
    if old is not None:
        assert old in source, "mutation target %r is gone; fix this test" % (old,)
        source = source.replace(old, new, 1)
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return str(path)


def test_two_identical_copies_redact_every_probe_the_same_way(tmp_path):
    assert sync_contract.compare_redaction(
        redact.__file__, _redact_copy(tmp_path)) == []


def test_a_pattern_dropped_from_one_copy_is_drift(tmp_path):
    """The realistic miss: a fix widens one copy's patterns and not the
    other's. Here the JSON-quoted name goes back to how it was written
    before Cycle 170, in one copy only."""
    other = _redact_copy(tmp_path,
                         old=r'r"(\"?\s*[=:]\s*\"?)"',
                         new=r'r"(\s*[=:]\s*\"?)"')
    drifted = sync_contract.compare_redaction(redact.__file__, other)
    labels = [label for label, _, _ in drifted]
    assert "named value, quoted json" in labels, labels
    # Named, not `drifted[0]`. Only one probe drifts under this mutation
    # today, so indexing would pass -- and would silently start checking a
    # different probe's answers the moment another JSON-shaped probe is
    # added to the table.
    _, left_answer, right_answer = [
        d for d in drifted if d[0] == "named value, quoted json"][0]
    assert "[redacted:" in left_answer
    assert "[redacted:" not in right_answer


def test_a_label_reworded_in_one_copy_is_drift(tmp_path):
    """The marker is what Edvard reads, so the two feeds saying different
    words for the same removal is a real difference, not cosmetics."""
    other = _redact_copy(tmp_path, old='("aws key id"', new='("aws access key"')
    labels = [label for label, _, _ in
              sync_contract.compare_redaction(redact.__file__, other)]
    assert "aws access key id" in labels, labels


def test_a_copy_with_no_redact_is_an_error_not_agreement(tmp_path):
    """Same reasoning as ContractRouterMissing: a filter that cannot be
    found has not been shown to agree with anything."""
    other = _redact_copy(tmp_path, old="def redact(text):", new="def scrub(text):")
    with pytest.raises(sync_contract.ContractRedactorMissing):
        sync_contract.compare_redaction(redact.__file__, other)


def test_two_copies_that_both_stopped_redacting_is_an_error(tmp_path):
    """The guard that makes this comparison evidence rather than decoration.

    Both copies pass everything through, so every probe matches and the
    difference list is empty -- which is exactly the clean run a credential
    filter must not be able to produce by doing nothing.
    """
    off = "    return text\n    if not isinstance(text, str) or not text:"
    a = _redact_copy(tmp_path,
                     old="    if not isinstance(text, str) or not text:",
                     new=off, name="a.py")
    b = _redact_copy(tmp_path,
                     old="    if not isinstance(text, str) or not text:",
                     new=off, name="b.py")
    with pytest.raises(sync_contract.ContractRedactorMissing) as caught:
        sync_contract.compare_redaction(a, b)
    assert "unchanged" in str(caught.value)


def test_two_copies_that_both_redact_everything_is_an_error(tmp_path):
    """The other direction, and the one Edvard's keep-everything rule is
    about: a filter that eats ordinary prose also agrees with itself."""
    everything = ('    if not isinstance(text, str) or not text:\n'
                  '        return text\n'
                  '    return "[redacted: value]"\n'
                  '    if False:')
    a = _redact_copy(tmp_path,
                     old="    if not isinstance(text, str) or not text:",
                     new=everything, name="a.py")
    b = _redact_copy(tmp_path,
                     old="    if not isinstance(text, str) or not text:",
                     new=everything, name="b.py")
    with pytest.raises(sync_contract.ContractRedactorMissing) as caught:
        sync_contract.compare_redaction(a, b)
    assert "over-redacting" in str(caught.value)


def test_every_probe_that_claims_to_be_a_credential_is_one(tmp_path):
    """The table itself, checked against the live filter.

    A `must_change` probe that the real redact() does not touch would make
    the guard above fire on every run and the next cycle would relax the
    guard. This is what keeps the table honest instead.
    """
    for label, text, must_change in sync_contract.REDACTION_PROBES:
        out = redact.redact(text)
        assert (out != text) == must_change, label


# ---------------------------------------------------------------------------
# The third pair: the two CI workflows (Cycle 172)
# ---------------------------------------------------------------------------
#
# The bridge's copy lives in another repository, so as with the other two
# pairs these mutate a copy of the live file rather than a stub. A stub
# workflow is the double that agrees with whatever it is handed, and it would
# also hide the thing this pair is for: the probes have to keep resolving
# against the real file as it is actually written.

WORKFLOW_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(sync_contract.__file__))),
    ".github", "workflows", "build.yaml")
WORKFLOW_SOURCE = open(WORKFLOW_PATH, encoding="utf-8").read()


def _workflow_copy(tmp_path, old=None, new=None, name="build_copy.yaml"):
    source = WORKFLOW_SOURCE
    if old is not None:
        assert old in source, "mutation target %r is gone; fix this test" % (old,)
        source = source.replace(old, new, 1)
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return str(path)


def test_two_identical_pipelines_agree_on_every_probe(tmp_path):
    assert sync_contract.compare_workflow(
        WORKFLOW_PATH, _workflow_copy(tmp_path)) == []


def test_every_probe_finds_something_in_the_live_workflow():
    """The guard against a comparison of two Nones.

    `compare_workflow` raises when a probe reads nothing on both sides, so
    this is really a check that the raise cannot be provoked by the file as
    it stands -- rename a job and the pair goes yellow, not green.
    """
    doc = sync_contract._parse_workflow(WORKFLOW_PATH)
    for label, probe in sync_contract.WORKFLOW_PROBES:
        assert probe(doc) is not None, label


def test_a_changed_concurrency_group_is_drift(tmp_path):
    """The merge race the group exists to stop, reintroduced on one side."""
    other = _workflow_copy(tmp_path, old="  group: build-${{ github.ref }}",
                           new="  group: build-${{ github.sha }}")
    drifted = sync_contract.compare_workflow(WORKFLOW_PATH, other)
    assert [label for label, _, _ in drifted] == ["concurrency"]


def test_cancel_in_progress_flipped_on_one_side_is_drift(tmp_path):
    other = _workflow_copy(tmp_path, old="  cancel-in-progress: false",
                           new="  cancel-in-progress: true")
    assert [label for label, _, _ in
            sync_contract.compare_workflow(WORKFLOW_PATH, other)] == ["concurrency"]


def test_the_gap_this_pair_found_on_its_first_run(tmp_path):
    """The bridge's secret scan had no failure guidance and the runner's did.

    Not a hypothetical: this is what the pair reported the first time it ran,
    on 2026-08-13. gitleaks reads git history, so the difference is which
    repo tells you to fix a leak before it reaches main and becomes a history
    rewrite.
    """
    scan = "          gitleaks detect --source . --no-banner --redact"
    start = WORKFLOW_SOURCE.index(scan + " || {")
    end = WORKFLOW_SOURCE.index("\n          }\n", start) + len("\n          }\n")
    other = _workflow_copy(tmp_path, old=WORKFLOW_SOURCE[start:end], new=scan + "\n")
    assert [label for label, _, _ in
            sync_contract.compare_workflow(WORKFLOW_PATH, other)] == ["secret scan"]


def test_a_language_specific_test_step_is_not_drift(tmp_path):
    """The half that is meant to differ. The bridge compiles a different
    package and has no browser suite; if that read as drift the pair would be
    red permanently and somebody would delete it."""
    other = _workflow_copy(tmp_path, old="      - run: python -m compileall agora_runner run.py",
                           new="      - run: python -m compileall bridge run.py")
    assert sync_contract.compare_workflow(WORKFLOW_PATH, other) == []


def test_a_reworded_comment_is_not_drift(tmp_path):
    """Why this pair is parsed rather than diffed: the race comment above
    `concurrency` is written from each repo's point of view on purpose."""
    other = _workflow_copy(tmp_path, old="# Measured 2026-08-12 in the bridge",
                           new="# Measured on some other day somewhere else")
    assert sync_contract.compare_workflow(WORKFLOW_PATH, other) == []


def test_a_dropped_pipeline_job_is_drift(tmp_path):
    other = _workflow_copy(tmp_path, old="  vault-drift:", new="  vault-drift-disabled:")
    assert [label for label, _, _ in
            sync_contract.compare_workflow(WORKFLOW_PATH, other)] == ["pipeline jobs"]


def test_a_job_renamed_on_both_sides_is_an_error_not_agreement(tmp_path):
    """The failure mode every probe here is one line away from.

    Both probes read `jobs["build-push"]`; rename it in both copies and both
    read None, which compares equal. That is a green check that compared
    nothing, and it is exactly Cycle 53's mistake in miniature.
    """
    mutation = dict(old="  build-push:", new="  build-and-push:")
    a = _workflow_copy(tmp_path, name="a.yaml", **mutation)
    b = _workflow_copy(tmp_path, name="b.yaml", **mutation)
    with pytest.raises(sync_contract.ContractWorkflowUnreadable) as caught:
        sync_contract.compare_workflow(a, b)
    assert "build-push job" in str(caught.value)


def test_an_unparseable_workflow_is_exit_two_not_a_traceback(tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("jobs:\n  test:\n   - a\n  - b\n", encoding="utf-8")
    assert sync_contract.check_pair(sync_contract._WORKFLOW_STAGES, WORKFLOW_PATH, str(broken)) == 2


def test_every_registered_pair_is_a_stage_list_the_one_loop_can_drive(capsys):
    """The three pairs used to be three hand-written copies of the same
    printing loop, and `main` picked one by name. Now they are stage lists
    and `check_pair` is the only copy -- so a pair registered as anything
    else, or a stage missing a field the loop reads, is a CLI that crashes
    on a repo rather than a CLI that reports on it.
    """
    for label, stages in sync_contract._CHECKERS.items():
        assert all(isinstance(s, sync_contract._Stage) for s in stages), label
        for stage in stages:
            assert callable(stage.compare) and callable(stage.render), label
            assert isinstance(stage.advice, str) and stage.advice, label
            assert issubclass(tuple(stage.errors)[0], Exception), label


def test_a_drifting_redaction_pair_exits_one_through_the_shared_loop(
        tmp_path, capsys):
    """Redaction was its own copy of the loop, with its own drift wording and
    -- alone of the three -- a success line printed without `flush`, which is
    the thing both other copies carried a comment explaining. Driving it
    through `check_pair` is what makes those one decision instead of three.
    """
    other = _redact_copy(tmp_path,
                         old=r'r"(\"?\s*[=:]\s*\"?)"',
                         new=r'r"(\s*[=:]\s*\"?)"')
    assert sync_contract.check_pair(
        sync_contract._REDACTION_STAGES, redact.__file__, other) == 1
    err = capsys.readouterr().err
    assert "redaction: 1 question(s) answered differently" in err
    assert "named value, quoted json" in err
    assert "WORKFLOW_PROBES" not in err   # the other pair's advice
    # Rendered with `repr`, as the hand-written copy's `%r` did. A redacted
    # string differs from its neighbour by a marker and by whitespace, and
    # `str` hides the whitespace half of that.
    assert re.search(r": '.*'$", err, re.M), err


def test_the_pair_is_registered_so_the_cli_actually_runs_it():
    labels = [label for label, _, _ in sync_contract.PAIRS]
    assert "ci workflow" in labels
    assert set(labels) <= set(sync_contract._CHECKERS)


# ---------------------------------------------------------------------------
# Writes: the last unpinned half of the vault pair.
# ---------------------------------------------------------------------------

# Module-level names `_put_raw` needs before the class body is executed --
# `_ANY_REV` is a default argument, so it has to exist by then.
_WRITE_PRELUDE = '''
import time


class VaultUnreadableDocument(Exception):
    pass


_ANY_REV = object()


def _split_chunks(content):
    """One chunk. The probe's content is far under CHUNK_MIN_BYTES and real
    chunking is pinned by the syntax comparison, so a faithful stub here
    would be a second copy of something already checked.

    Empty content is `[""]` and not `[]`, which is what both real copies
    return -- a detail no row exercises today and the reason it is written
    down anyway: the first row to write an empty file would otherwise see
    the runner produce one chunk and this stub produce none, and read a
    fixture's shortcut as drift in the client."""
    return [content] if content else [""]
'''

# A faithful copy of `VaultClient._put_raw` and the three collaborators it
# reaches CouchDB through, in the bridge's own shape. A stub rather than the
# real file for the reason `_bridge_copy` gives -- the other repo is not
# present in this test run and the cross-repo comparison is CI's job. What
# keeps it honest is the first test below: if the runner's write changes and
# this does not, that test goes red rather than the check quietly comparing
# something else.
BRIDGE_WRITE_STUB = _WRITE_PRELUDE + BRIDGE_STUB + '''
    def _doc(self, method, doc_id, body=None, db=None):
        raise AssertionError("the probe is meant to replace this")

    def _chunk_id_for(self, content_bytes):
        try:
            import xxhash
            return f"h:{xxhash.xxh64(content_bytes).hexdigest()}"
        except Exception:
            import hashlib
            return f"h:{hashlib.sha256(content_bytes).hexdigest()[:16]}"

    def _existing_chunk_ids(self, chunk_ids, db):
        keys = sorted(set(chunk_ids))
        if not keys:
            return set()
        status, body = self._doc("POST", "_all_docs", {"keys": keys}, db=db)
        if status != 200:
            return set()
        return {
            row["key"] for row in body.get("rows", [])
            if "error" not in row and not (row.get("value") or {}).get("deleted")
        }

    def _doc_to_overwrite(self, doc_id, db=None):
        status, doc = self._doc("GET", doc_id, db=db)
        if status == 200:
            return doc
        if status == 404:
            return None
        raise VaultUnreadableDocument(f"{doc_id}: HTTP {status}")

    def _put_raw(self, path, content, existing=None, if_rev=_ANY_REV):
        path = path.lower()
        db = self.db_for(path)
        now_ms = int(time.time() * 1000)
        content_bytes = content.encode("utf-8")
        chunk_texts = _split_chunks(content)
        chunk_ids = [self._chunk_id_for(t.encode("utf-8")) for t in chunk_texts]
        if existing is None:
            try:
                existing = self._doc_to_overwrite(path, db=db)
            except VaultUnreadableDocument as e:
                return f"FAILED(unreadable: {e})"
        already = self._existing_chunk_ids(chunk_ids, db)
        written = set()
        for chunk_id, text in zip(chunk_ids, chunk_texts):
            if chunk_id in already or chunk_id in written:
                continue
            chunk = {"_id": chunk_id, "data": text, "type": "leaf", "children": []}
            chunk_status, _ = self._doc("PUT", chunk_id, chunk, db=db)
            if chunk_status == 409:
                written.add(chunk_id)
                continue
            if chunk_status not in (200, 201):
                return f"FAILED(chunk {chunk_id}: {chunk_status})"
            written.add(chunk_id)
        doc = {
            "_id": path, "path": path, "data": "", "children": chunk_ids,
            "size": len(content_bytes), "ctime": now_ms, "mtime": now_ms,
            "type": "plain", "eden": {},
        }
        if existing is not None:
            doc["_rev"] = existing["_rev"]
            doc["ctime"] = existing.get("ctime", now_ms)
        if if_rev is not _ANY_REV:
            if if_rev is None:
                doc.pop("_rev", None)
            else:
                doc["_rev"] = if_rev
        status, _ = self._doc("PUT", path, doc, db=db)
        if status in (200, 201):
            return "written"
        if status == 409:
            return f"FAILED(409 conflict: {path} changed since it was read)"
        return f"FAILED({status})"
'''

# The decisions each mutation below breaks, in each copy's own shape. The
# runner has them at one indent and the bridge at two, which is the same
# difference that made a syntax comparison useless here.
_CTIME_RUNNER = '        doc["ctime"] = existing.get("ctime", now_ms)'
_CTIME_BRIDGE = '            doc["ctime"] = existing.get("ctime", now_ms)'
_IFREV_RUNNER = "    if if_rev is not _ANY_REV:"
_IFREV_BRIDGE = "        if if_rev is not _ANY_REV:"
_CHUNK409_RUNNER = "        if chunk_status == 409:"
_CHUNK409_BRIDGE = "            if chunk_status == 409:"
_LOWER_RUNNER = "    path = path.lower()"
_LOWER_BRIDGE = "        path = path.lower()"


def _write_pair(tmp_path, runner=(None, None), bridge=(None, None)):
    """`(runner path, bridge path)`, each optionally mutated once."""
    runner_path = str(tmp_path / "runner_vault.py")
    source = RUNNER_SOURCE
    if runner[0] is not None:
        source = _mutated(runner[0], runner[1])
    open(runner_path, "w", encoding="utf-8").write(source)
    return runner_path, _bridge_copy(
        tmp_path, source=BRIDGE_WRITE_STUB, old=bridge[0], new=bridge[1])


def test_the_two_copies_write_every_probe_the_same_way(tmp_path):
    assert sync_contract.compare_writes(*_write_pair(tmp_path)) == []


def test_both_database_configurations_are_driven(tmp_path):
    """Routing on and routing off are separate early returns in both copies,
    so a probe that only ran one of them would cover half the client while
    reporting a whole number."""
    runner_path, bridge_path = _write_pair(
        tmp_path, bridge=(_LOWER_BRIDGE, "        path = path"))
    drifted = sync_contract.compare_writes(runner_path, bridge_path)
    labels = {q.split(":")[0] for q, _, _ in drifted}
    assert labels == {"writes, routing on", "writes, routing off"}


def test_every_row_of_the_table_is_actually_asked(tmp_path):
    """The count the writes stage prints is the count that ran.

    Written because the assembly probe's success line understated its own
    run by half and nothing noticed until a second reader read the
    arithmetic. Asserting against the answer keys rather than against the
    same arithmetic the message uses -- the message is what is under test.
    """
    runner_path, bridge_path = _write_pair(tmp_path)
    asked = []
    original = sync_contract._write_answers

    def spy(ask):
        answers = original(ask)
        asked.append(sorted(answers))
        return answers

    sync_contract._write_answers = spy
    try:
        assert sync_contract.compare_writes(runner_path, bridge_path) == []
    finally:
        sync_contract._write_answers = original
    want = sorted(row[0] for row in sync_contract._WRITE_QUESTIONS)
    # One entry per copy per configuration, every one of them the whole table.
    assert asked == [want] * (2 * len(sync_contract.ROUTING_CONFIGS))
    assert len(want) * len(sync_contract.ROUTING_CONFIGS) == 22


def test_the_success_line_counts_what_actually_ran(tmp_path):
    """The Cycle 156 bug itself, which no test in this file had ever pinned.

    The assembly probe's success line multiplied its row count by the
    configurations for a while and then did not, and nothing went red --
    every test here asserts on the comparison's return value, and none of
    them reads what the check prints. A count that understates its own run
    is a check quietly under-claiming, which is the same class of thing as
    over-claiming with the friendlier direction. Asserted against the number
    of driven writes rather than against the same arithmetic the message
    uses, so re-deriving it in the message cannot make this agree.
    """
    runner_path, bridge_path = _write_pair(tmp_path)
    stage = next(s for s in sync_contract._VAULT_STAGES
                 if s.label == "vault writes")
    assert sync_contract.compare_writes(runner_path, bridge_path) == []
    assert stage.summary(runner_path, bridge_path) == (
        "22 probed writes made the same requests in both copies")


def test_losing_the_ctime_carry_forward_on_one_side_is_drift(tmp_path):
    """Cycle 167's bug, which nothing prevented coming back.

    The write still succeeds and still returns "written". The only visible
    trace is that every overwritten file claims it was created today.
    """
    drifted = sync_contract.compare_writes(*_write_pair(
        tmp_path, bridge=(_CTIME_BRIDGE, "            pass")))
    assert [q for q, _, _ in drifted if "carries the old ctime" in q]


def test_both_copies_losing_the_ctime_carry_forward_is_an_error(tmp_path):
    """The reason this probe states expected answers instead of only
    comparing. Both copies wrong the same way agree on every row."""
    with pytest.raises(sync_contract.ContractWriterMissing) as caught:
        sync_contract.compare_writes(*_write_pair(
            tmp_path,
            runner=(_CTIME_RUNNER, "        pass"),
            bridge=(_CTIME_BRIDGE, "            pass")))
    assert "agreement is not evidence" in str(caught.value)


def test_letting_the_lookup_beat_if_rev_on_one_side_is_drift(tmp_path):
    """The silent clobber `if_rev` exists to stop: the write succeeds, the
    caller is told "written", and the entry it raced disappears."""
    drifted = sync_contract.compare_writes(*_write_pair(
        tmp_path, bridge=(_IFREV_BRIDGE, "        if False:")))
    questions = [q for q, _, _ in drifted]
    assert [q for q in questions if "if_rev beats the lookup" in q]
    assert [q for q in questions if "if_rev=None" in q]


def test_both_copies_ignoring_if_rev_is_an_error_not_agreement(tmp_path):
    with pytest.raises(sync_contract.ContractWriterMissing):
        sync_contract.compare_writes(*_write_pair(
            tmp_path,
            runner=(_IFREV_RUNNER, "    if False:"),
            bridge=(_IFREV_BRIDGE, "        if False:")))


def test_treating_a_chunk_conflict_as_failure_on_one_side_is_drift(tmp_path):
    drifted = sync_contract.compare_writes(*_write_pair(
        tmp_path, bridge=(_CHUNK409_BRIDGE, "            if False:")))
    assert [q for q, _, _ in drifted if "409 on a chunk" in q]


def test_both_copies_treating_a_chunk_conflict_as_failure_is_an_error(
        tmp_path):
    with pytest.raises(sync_contract.ContractWriterMissing):
        sync_contract.compare_writes(*_write_pair(
            tmp_path,
            runner=(_CHUNK409_RUNNER, "        if False:"),
            bridge=(_CHUNK409_BRIDGE, "            if False:")))


def test_pointing_the_file_document_at_a_chunk_that_failed_is_an_error(
        tmp_path):
    """The failure that is silent until somebody reads the file. Both copies
    doing it agree, so only the expected sequence catches it."""
    with pytest.raises(sync_contract.ContractWriterMissing):
        sync_contract.compare_writes(*_write_pair(
            tmp_path,
            runner=('            return f"FAILED(chunk {chunk_id}: {chunk_status})"',
                    "            written.add(chunk_id)"),
            bridge=('                return f"FAILED(chunk {chunk_id}: {chunk_status})"',
                    "                written.add(chunk_id)")))


def test_a_copy_that_stops_lowering_the_path_is_drift(tmp_path):
    drifted = sync_contract.compare_writes(*_write_pair(
        tmp_path, bridge=(_LOWER_BRIDGE, "        path = path")))
    assert [q for q, _, _ in drifted if "lowered before anything" in q]


def test_skipping_the_existence_scan_is_drift(tmp_path):
    """Not a wrong answer -- a missing request. The write still returns
    "written" and still stores the right bytes; what it loses is the dedup
    that stops an append leaving a second copy of the file behind."""
    drifted = sync_contract.compare_writes(*_write_pair(
        tmp_path, bridge=("        keys = sorted(set(chunk_ids))\n"
                          "        if not keys:\n"
                          "            return set()",
                          "        return set()")))
    # Named rows, not just "something differed": the scan disappearing shows
    # up on every row that expects it, and a bare truthiness assertion would
    # also pass if the mutation had broken something else entirely.
    questions = [q for q, _, _ in drifted]
    assert [q for q in questions if "new file, unconditional" in q]
    assert [q for q in questions if "already exists is not rewritten" in q]


def test_a_chunk_id_that_differs_for_identical_bytes_is_drift(tmp_path):
    """Two copies that hash the same text differently stop reusing each
    other's chunks, which brings the write amplification straight back.

    The mutation replaces the whole method rather than the xxhash line it
    first targeted. `_chunk_id_for` picks xxhash if it can import it and
    falls back to sha256 if it cannot, and CI has no xxhash -- so mutating
    the fast path left the method computing a perfectly correct id, and this
    test passed on the author's box and failed in the build. A mutation
    aimed at a branch is only as honest as the environment that takes it.
    """
    drifted = sync_contract.compare_writes(*_write_pair(
        tmp_path, bridge=("    def _chunk_id_for(self, content_bytes):\n",
                          "    def _chunk_id_for(self, content_bytes):\n"
                          '        return "h:notthesame"\n')))
    assert [q for q, _, _ in drifted if "chunk id for identical bytes" in q]


def test_a_copy_with_no_writer_is_an_error_not_agreement(tmp_path):
    runner_path, _ = _write_pair(tmp_path)
    bridge_path = _bridge_copy(tmp_path, source=BRIDGE_STUB)
    with pytest.raises(sync_contract.ContractWriterMissing):
        sync_contract.compare_writes(runner_path, bridge_path)


def test_real_write_drift_is_not_masked_by_the_guard(tmp_path):
    """Same shape as #153's finding on routing: a run that finds a real
    difference must report it rather than an instrumentation error, even
    when the rows the two agreed on are also wrong."""
    drifted = sync_contract.compare_writes(*_write_pair(
        tmp_path,
        runner=(_CTIME_RUNNER, "        pass"),
        bridge=(_LOWER_BRIDGE, "        path = path")))
    questions = [q for q, _, _ in drifted]
    assert [q for q in questions if "lowered before anything" in q]
    assert [q for q in questions if "carries the old ctime" in q]


def test_the_clock_is_put_back_after_the_comparison(tmp_path):
    before = time.time
    sync_contract.compare_writes(*_write_pair(tmp_path))
    assert time.time is before
    assert time.time() > sync_contract._WRITE_NOW


def test_the_clock_is_put_back_even_when_the_comparison_raises(tmp_path):
    before = time.time
    runner_path, _ = _write_pair(tmp_path)
    with pytest.raises(sync_contract.ContractWriterMissing):
        sync_contract.compare_writes(
            runner_path, _bridge_copy(tmp_path, source=BRIDGE_STUB))
    assert time.time is before


# ---------------------------------------------------------------------------
# The fifth stage: what each copy reports about the databases it can reach.
# ---------------------------------------------------------------------------

# The real bridge's `database_health`, on top of the routing stub above --
# same discipline as `BRIDGE_WRITE_STUB`, and the first test below is what
# keeps it honest. It reaches CouchDB through the module-level `_req` rather
# than through `_doc`, which is exactly why this stage needed its own driver.
BRIDGE_HEALTH_STUB = BRIDGE_STUB + '''
import urllib.parse

HEALTH_TIMEOUT_SECONDS = 5
# The runner's own list, not a shortened one: `routes` is part of the report
# both copies return, so a stub probing different paths would report drift
# that has nothing to do with health.
HEALTH_PROBE_PATHS = ''' + repr(vault.HEALTH_PROBE_PATHS) + '''


def _req(method, base, db, auth, path, body=None, timeout=60):
    raise AssertionError("the driver replaces this")


def _database_health(self):
    names = {"main": self.db}
    if self.nova_db:
        names["nova"] = self.nova_db
    databases = {}
    for role, name in names.items():
        entry = {"name": name, "reachable": False, "doc_count": None, "error": None}
        try:
            status, info = _req("GET", self.base, urllib.parse.quote(name, safe=""),
                                self.auth, "", timeout=HEALTH_TIMEOUT_SECONDS)
            if status == 200:
                entry["reachable"] = True
                entry["doc_count"] = info.get("doc_count")
            else:
                entry["error"] = f"HTTP {status}"
        except Exception as e:
            entry["error"] = str(e)[:200]
        databases[role] = entry
    return {
        "routing_enabled": bool(self.nova_db),
        "databases": databases,
        "routes": [{"path": p, "database": self.db_for(p)} for p in HEALTH_PROBE_PATHS],
    }


VaultClient.database_health = _database_health
VaultClient.base = "http://vault-contract.invalid"
VaultClient.auth = "probe"
'''

# The decisions each mutation below breaks. Both copies carry them at their
# own indent, which is the difference that makes a syntax comparison useless
# here and is why this is driven rather than parsed.
_REACHABLE = '                entry["reachable"] = True'
_STATUS_OK = "            if status == 200:"
_COUNT_RUNNER = '                entry["doc_count"] = info.get("doc_count")'
_COUNT_BRIDGE = '                entry["doc_count"] = info.get("doc_count")'
_TRUNCATE = '            entry["error"] = str(e)[:200]'
_SHORT_TIMEOUT_RUNNER = "                timeout=HEALTH_TIMEOUT_SECONDS,"
_ROUTES_RUNNER = ('        "routes": [{"path": p, "database": db_for(p)} '
                  'for p in HEALTH_PROBE_PATHS],')
_ROUTES_BRIDGE = ('        "routes": [{"path": p, "database": self.db_for(p)} '
                  'for p in HEALTH_PROBE_PATHS],')
_SHORT_TIMEOUT_BRIDGE = 'self.auth, "", timeout=HEALTH_TIMEOUT_SECONDS)'
_ROUTING_FLAG_BRIDGE = '        "routing_enabled": bool(self.nova_db),'
_ROUTING_FLAG_RUNNER = '        "routing_enabled": bool(COUCHDB_NOVA_DB),'


def _health_pair(tmp_path, runner=(None, None), bridge=(None, None)):
    """`(runner path, bridge path)`, each optionally mutated once."""
    runner_path = str(tmp_path / "runner_vault.py")
    source = RUNNER_SOURCE
    if runner[0] is not None:
        source = _mutated(runner[0], runner[1])
    open(runner_path, "w", encoding="utf-8").write(source)
    return runner_path, _bridge_copy(
        tmp_path, source=BRIDGE_HEALTH_STUB, old=bridge[0], new=bridge[1])


def test_the_two_copies_report_health_the_same_way(tmp_path):
    assert sync_contract.compare_health(*_health_pair(tmp_path)) == []


def test_both_database_configurations_are_driven_for_health(tmp_path):
    """Routing on and routing off decide how many stores are probed at all,
    so a run that drove one of them would report a whole number for half a
    client."""
    runner_path, bridge_path = _health_pair(
        tmp_path, bridge=(_SHORT_TIMEOUT_BRIDGE, 'self.auth, "", timeout=60)'))
    drifted = sync_contract.compare_health(runner_path, bridge_path)
    labels = {q.split(":")[0] for q, _, _ in drifted}
    assert labels == {"health, routing on", "health, routing off"}


def test_every_health_question_is_actually_asked(tmp_path):
    """The count the success line prints is the count that ran -- asserted
    against the answer keys rather than against the same arithmetic the
    message uses, because the message is what is under test."""
    runner_path, bridge_path = _health_pair(tmp_path)
    asked = []
    original = sync_contract._health_answers

    def spy(ask):
        answers = original(ask)
        asked.append(sorted(answers))
        return answers

    sync_contract._health_answers = spy
    try:
        assert sync_contract.compare_health(runner_path, bridge_path) == []
    finally:
        sync_contract._health_answers = original
    want = sorted(row[0] for row in sync_contract._HEALTH_QUESTIONS)
    assert asked == [want] * (2 * len(sync_contract.ROUTING_CONFIGS))
    assert len(want) * len(sync_contract.ROUTING_CONFIGS) == 10


@pytest.mark.parametrize("old,new", [
    # A 404 reported as a reachable database: the friendliest possible lie,
    # and the page it feeds is what a migration is judged by.
    (_STATUS_OK, "            if status >= 200:"),
    # The reachable flag itself, which the stage's own advice names first and
    # which nothing here had mutated -- the row above only broke the status
    # check guarding it. Second reader on #158.
    (_REACHABLE, '                entry["reachable"] = False'),
    # A missing doc_count read as zero rather than unknown.
    (_COUNT_BRIDGE, '                entry["doc_count"] = info.get("doc_count", 0)'),
    # The error text no longer truncated -- one CouchDB traceback becomes the
    # whole status payload.
    (_TRUNCATE, '            entry["error"] = str(e)'),
    # The short probe timeout replaced by the 60s default every other call
    # uses, which is the whole reason the constant exists.
    (_SHORT_TIMEOUT_BRIDGE, 'self.auth, "", timeout=60)'),
    # The flag that says whether a second store exists at all.
    (_ROUTING_FLAG_BRIDGE, '        "routing_enabled": True,'),
    # The routes comprehension, as opposed to `db_for` -- see the both-copies
    # version below for why these are not the same bug.
    (_ROUTES_BRIDGE, '        "routes": [{"path": p, "database": self.db} '
                     'for p in HEALTH_PROBE_PATHS],'),
])
def test_a_broken_health_report_in_one_copy_is_drift(tmp_path, old, new):
    assert sync_contract.compare_health(
        *_health_pair(tmp_path, bridge=(old, new))) != []


def test_the_runner_losing_the_short_probe_timeout_is_drift(tmp_path):
    """Every mutation above breaks the bridge stub. This one breaks the real
    runner, so a driver that only ever read one side would pass all of them
    and fail this."""
    assert sync_contract.compare_health(*_health_pair(
        tmp_path,
        runner=(_SHORT_TIMEOUT_RUNNER, "                timeout=60,"))) != []


def test_both_copies_routing_every_path_to_one_store_is_an_error(tmp_path):
    """The gap the second reader on #158 demonstrated.

    `db_for` keeps answering correctly, so `compare_routing` stays green; it
    is the routes comprehension inside the report that is wrong, in both
    copies. The first version of this stage collapsed that list to a single
    token whenever it was non-empty, which scored this for free.
    """
    runner_path, bridge_path = _health_pair(
        tmp_path,
        runner=(_ROUTES_RUNNER, '        "routes": [{"path": p, "database": '
                                'COUCHDB_DB} for p in HEALTH_PROBE_PATHS],'),
        bridge=(_ROUTES_BRIDGE, '        "routes": [{"path": p, "database": '
                                'self.db} for p in HEALTH_PROBE_PATHS],'))
    assert sync_contract.compare_routing(runner_path, bridge_path) == []
    with pytest.raises(sync_contract.ContractHealthMissing) as exc:
        sync_contract.compare_health(runner_path, bridge_path)
    assert "agreement is not evidence" in str(exc.value)


def test_both_copies_dropping_probe_paths_is_an_error(tmp_path):
    """The other half of the same gap: the report still names both databases,
    so a guard that only checked *which* stores appear would pass."""
    runner_path, bridge_path = _health_pair(
        tmp_path,
        runner=(_ROUTES_RUNNER, '        "routes": [{"path": p, "database": '
                                'db_for(p)} for p in HEALTH_PROBE_PATHS[:2]],'),
        bridge=(_ROUTES_BRIDGE, '        "routes": [{"path": p, "database": '
                                'self.db_for(p)} for p in HEALTH_PROBE_PATHS[:2]],'))
    with pytest.raises(sync_contract.ContractHealthMissing):
        sync_contract.compare_health(runner_path, bridge_path)


def test_both_copies_hardcoding_routing_enabled_is_an_error(tmp_path):
    """`routing_enabled` is the one field the status page reads to decide
    whether to show a second store at all."""
    runner_path, bridge_path = _health_pair(
        tmp_path,
        runner=(_ROUTING_FLAG_RUNNER, '        "routing_enabled": True,'),
        bridge=(_ROUTING_FLAG_BRIDGE, '        "routing_enabled": True,'))
    with pytest.raises(sync_contract.ContractHealthMissing):
        sync_contract.compare_health(runner_path, bridge_path)


def test_a_multi_line_names_drift_stays_readable(capsys):
    """The names stage answers with a normalised source body, so `repr` turns
    a readable diff into one escaped string. Every driven stage answers with
    short scalars, where `repr` is what makes a trailing space visible -- so
    the loop asks the stage, rather than picking one for all five."""
    stage = sync_contract._Stage
    saved = sync_contract._VAULT_STAGES
    body = "def f():\n    return 1\n"
    sync_contract._VAULT_STAGES = (
        stage("names", lambda l, r: [("f", body, "def f():\n    return 2\n")],
              (LookupError,), "advice", lambda l, r: "ok", str),
    )
    try:
        assert sync_contract.check_pair(sync_contract._VAULT_STAGES, "l.py", "r.py") == 1
    finally:
        sync_contract._VAULT_STAGES = saved
    err = capsys.readouterr().err
    assert "    return 1" in err
    assert "\\n" not in err


def test_the_second_store_is_still_probed_after_the_first_one_raises(tmp_path):
    """The row that matters most during a migration. A copy that let the
    exception out of the loop reports nothing about the database files are
    being moved *into* -- and it still returns a well-formed report."""
    runner_path, bridge_path = _health_pair(
        tmp_path, bridge=("        except Exception as e:",
                          "            break\n        except Exception as e:"))
    assert sync_contract.compare_health(runner_path, bridge_path) != []


def test_the_guard_fires_when_both_copies_are_wrong_the_same_way(tmp_path):
    """Agreement is not evidence here. Break both copies identically and the
    comparison must refuse to report a clean run rather than return []."""
    runner_path, bridge_path = _health_pair(
        tmp_path,
        runner=(_COUNT_RUNNER,
                '                entry["doc_count"] = info.get("doc_count", 0)'),
        bridge=(_COUNT_BRIDGE,
                '                entry["doc_count"] = info.get("doc_count", 0)'))
    with pytest.raises(sync_contract.ContractHealthMissing) as exc:
        sync_contract.compare_health(runner_path, bridge_path)
    assert "agreement is not evidence" in str(exc.value)


def test_both_copies_dropping_the_routes_table_is_an_error(tmp_path):
    """The report's routing table is what says where files are *going*
    during a migration. Two copies that both stopped returning it agree on
    an empty list, so the drift comparison alone would call that in sync."""
    runner_path, bridge_path = _health_pair(
        tmp_path,
        runner=('        "routes": [{"path": p, "database": db_for(p)} '
                'for p in HEALTH_PROBE_PATHS],', '        "routes": [],'),
        bridge=('        "routes": [{"path": p, "database": self.db_for(p)} '
                'for p in HEALTH_PROBE_PATHS],', '        "routes": [],'))
    with pytest.raises(sync_contract.ContractHealthMissing) as exc:
        sync_contract.compare_health(runner_path, bridge_path)
    assert "agreement is not evidence" in str(exc.value)


@pytest.mark.parametrize("field, value", [
    # Reachability the page renders as a green light, computed instead of
    # measured: `True` here regardless of what either store answered.
    ('"overall_healthy"', "True"),
    # A number the page would show beside a database, from nowhere.
    ('"doc_total"', "0"),
])
def test_a_field_both_copies_added_is_not_silently_unchecked(
        tmp_path, field, value):
    """A field added to the health report has to be stated in
    `_HEALTH_QUESTIONS` before this stage will report a clean run.

    The correctness check used to rebuild the report from three known keys,
    so anything else in it was invisible to the expectations -- and a field
    added to *both* copies at once is exactly the case those expectations
    exist for, since the drift comparison scores identical copies as in
    sync. Both mutations below put a value on the status page that no store
    was asked about.
    """
    added = "        %s: %s," % (field, value)
    runner_path, bridge_path = _health_pair(
        tmp_path,
        runner=(_ROUTING_FLAG_RUNNER, _ROUTING_FLAG_RUNNER + "\n" + added),
        bridge=(_ROUTING_FLAG_BRIDGE, _ROUTING_FLAG_BRIDGE + "\n" + added))
    with pytest.raises(sync_contract.ContractHealthMissing) as exc:
        sync_contract.compare_health(runner_path, bridge_path)
    assert "agreement is not evidence" in str(exc.value)
    assert field.strip('"') in str(exc.value)


def test_the_reported_difference_names_only_what_differs():
    """A whole health report is ~900 characters and a drifted row prints two
    of them, so the CI log has to be diffed by eye to find the one field
    that moved. Asserted against the narrowed structure, not against a
    length -- a shorter message that lost the field would pass that."""
    left = {"routing_enabled": True,
            "databases": {"main": {"name": "db", "doc_count": 4211}},
            "routes": [{"path": "a", "database": "db"}]}
    right = {"routing_enabled": True,
             "databases": {"main": {"name": "db", "doc_count": 0}},
             "routes": [{"path": "a", "database": "db"}]}
    assert sync_contract._differences_only(left, right) == (
        {"databases": {"main": {"doc_count": 4211}}},
        {"databases": {"main": {"doc_count": 0}}})


def test_a_field_one_copy_stopped_returning_is_named_not_dropped():
    """The narrowing walks both sides, so a key present on one only has to
    survive it -- that is a whole field disappearing from the report."""
    got = sync_contract._differences_only(
        {"a": 1, "reachable": True}, {"a": 1})
    assert got == ({"reachable": True}, {"reachable": "<missing>"})


def test_identical_answers_narrow_to_nothing():
    assert sync_contract._differences_only({"a": 1}, {"a": 1}) == (
        sync_contract._SAME, sync_contract._SAME)


@pytest.mark.parametrize("old,new", [
    ("            if status == 200:", "            if status >= 200:"),
    (_COUNT_BRIDGE, '                entry["doc_count"] = info.get("doc_count", 0)'),
    (_TRUNCATE, '            entry["error"] = str(e)'),
    (_SHORT_TIMEOUT_BRIDGE, 'self.auth, "", timeout=60)'),
])
def test_the_narrowing_never_makes_a_real_difference_look_equal(
        tmp_path, old, new):
    """The one way a message that prints less could be worse than one that
    prints too much. Every drifted row must still print two unequal sides."""
    drifted = sync_contract.compare_health(
        *_health_pair(tmp_path, bridge=(old, new)))
    assert drifted
    for _, left, right in drifted:
        assert left != right


def test_the_drift_rows_are_narrowed_before_they_are_printed(tmp_path):
    """`_differences_only` existing is not the same as it being used, and the
    row the CI log prints is the one that matters."""
    drifted = sync_contract.compare_health(*_health_pair(
        tmp_path, bridge=(_SHORT_TIMEOUT_BRIDGE, 'self.auth, "", timeout=60)')))
    assert drifted
    for _, left, right in drifted:
        # The report half is identical under this mutation -- only the
        # recorded timeout moved -- so an un-narrowed row carries the whole
        # routes table here and a narrowed one carries `<same>`.
        assert sync_contract._SAME in repr(left)
        assert "routes" not in repr(left)


def test_a_copy_with_no_health_report_is_undrivable_not_in_sync(tmp_path):
    """Same reasoning as every other stage: a renamed method is a "cannot
    compare", never a comparison that found nothing."""
    runner_path, bridge_path = _health_pair(
        tmp_path, bridge=("VaultClient.database_health = _database_health",
                          "VaultClient.gone = _database_health"))
    with pytest.raises(sync_contract.ContractHealthMissing):
        sync_contract.compare_health(runner_path, bridge_path)


def test_every_stage_of_the_vault_pair_is_reachable_from_the_list():
    """The list is the point of the refactor: a stage that is not in it is a
    check that silently stops running, and the old shape hid exactly that
    behind four functions calling each other by name."""
    labels = [s.label for s in sync_contract._VAULT_STAGES]
    assert labels == ["vault contract", "vault routing", "vault assembly",
                      "vault writes", "vault health"]
    for stage in sync_contract._VAULT_STAGES:
        assert callable(stage.compare)
        assert stage.errors and all(
            issubclass(e, Exception) for e in stage.errors)
        assert stage.advice.strip()
        assert stage.render in (str, repr)


def test_the_pair_stops_at_the_first_stage_that_drifts(capsys):
    """Every stage or none, which is the whole reason the list exists.

    Driven through a substituted stage list rather than through the real
    five: the point under test is the loop, and building five genuinely
    comparable copies to exercise it would test the drivers instead.
    """
    ran = []

    def clean(_l, _r):
        ran.append("clean")
        return []

    def dirty(_l, _r):
        ran.append("dirty")
        return [("a question", "left", "right")]

    def never(_l, _r):
        ran.append("never")
        return []

    stage = sync_contract._Stage
    saved = sync_contract._VAULT_STAGES
    sync_contract._VAULT_STAGES = (
        stage("first", clean, (LookupError,), "advice",
              lambda l, r: "ok", repr),
        stage("second", dirty, (LookupError,), "the advice",
              lambda l, r: "ok", repr),
        stage("third", never, (LookupError,), "advice",
              lambda l, r: "ok", repr),
    )
    try:
        assert sync_contract.check_pair(sync_contract._VAULT_STAGES, "left.py", "right.py") == 1
    finally:
        sync_contract._VAULT_STAGES = saved
    assert ran == ["clean", "dirty"]
    out = capsys.readouterr()
    assert "first: ok" in out.out
    assert "third" not in out.out
    assert "a question" in out.err and "the advice" in out.err


def test_a_stage_that_cannot_be_driven_is_two_not_one(capsys):
    """An unreadable copy is "cannot compare", never a comparison that found
    nothing -- and the stages after it do not get to report success."""
    ran = []

    def missing(_l, _r):
        raise sync_contract.ContractHealthMissing("renamed")

    def never(_l, _r):
        ran.append("never")
        return []

    stage = sync_contract._Stage
    saved = sync_contract._VAULT_STAGES
    sync_contract._VAULT_STAGES = (
        stage("first", missing, (sync_contract.ContractHealthMissing,),
              "advice", lambda l, r: "ok", repr),
        stage("second", never, (LookupError,), "advice",
              lambda l, r: "ok", repr),
    )
    try:
        assert sync_contract.check_pair(sync_contract._VAULT_STAGES, "left.py", "right.py") == 2
    finally:
        sync_contract._VAULT_STAGES = saved
    assert ran == []
    assert "first: renamed" in capsys.readouterr().err
