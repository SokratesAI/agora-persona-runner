"""The vault credentials are named differently in each pod, and both must work.

The runner pod exports `COUCHDB_*`; the bridge pod exports the same host, the
same `obsidian`/`nova` databases and the same admin user under `CDB_*`. Until
Cycle 2066 `agora_runner.config` read only the first set, so anything in the
package that touches the vault silently ran unauthenticated from the bridge --
`ask_push_log.record` said so, and `cycle_health` did not: it read an empty
journal, found no gaps in it, and reported a healthy loop.
"""

import importlib

import pytest


@pytest.fixture(autouse=True)
def _config_restored():
    """Reloading a module is not undone by monkeypatch -- put it back myself.

    Without this the last test in the file leaves `agora_runner.config` holding
    that test's fake credentials for every test that imports it afterwards.
    """
    yield
    import agora_runner.config as config
    importlib.reload(config)


def _config(monkeypatch, env):
    for name in (
        "COUCHDB_URL", "COUCHDB_USER", "COUCHDB_PASSWORD", "COUCHDB_DB",
        "COUCHDB_NOVA_DB", "CDB_BASE", "CDB_USER", "CDB_PASS", "CDB_DB",
        "CDB_NOVA_DB",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    import agora_runner.config as config
    return importlib.reload(config)


def test_bridge_names_are_read_when_the_runner_names_are_absent(monkeypatch):
    config = _config(monkeypatch, {
        "CDB_BASE": "http://couchdb.obsidian.svc.cluster.local:5984",
        "CDB_USER": "admin",
        "CDB_PASS": "s3cret",
        "CDB_DB": "obsidian",
        "CDB_NOVA_DB": "nova",
    })
    assert config.COUCHDB_URL == "http://couchdb.obsidian.svc.cluster.local:5984"
    assert config.COUCHDB_USER == "admin"
    assert config.COUCHDB_PASSWORD == "s3cret"
    assert config.COUCHDB_DB == "obsidian"
    assert config.COUCHDB_NOVA_DB == "nova"


def test_runner_names_win_wherever_they_are_set(monkeypatch):
    config = _config(monkeypatch, {
        "COUCHDB_USER": "runner", "COUCHDB_PASSWORD": "runner-pass",
        "COUCHDB_DB": "obsidian", "COUCHDB_NOVA_DB": "nova",
        "CDB_USER": "bridge", "CDB_PASS": "bridge-pass",
        "CDB_DB": "other", "CDB_NOVA_DB": "other-nova",
    })
    assert config.COUCHDB_USER == "runner"
    assert config.COUCHDB_PASSWORD == "runner-pass"
    assert config.COUCHDB_DB == "obsidian"
    assert config.COUCHDB_NOVA_DB == "nova"


def test_neither_set_keeps_the_old_defaults(monkeypatch):
    config = _config(monkeypatch, {})
    assert config.COUCHDB_URL == "http://couchdb.obsidian.svc.cluster.local:5984"
    assert config.COUCHDB_USER == ""
    assert config.COUCHDB_PASSWORD == ""
    assert config.COUCHDB_DB == "obsidian"
    # Empty means "one database", the pre-routing behaviour vault.db_for wants.
    assert config.COUCHDB_NOVA_DB == ""


def test_an_empty_runner_name_falls_through_rather_than_winning(monkeypatch):
    # A pod that sets COUCHDB_USER="" has not configured it; an empty string
    # authenticates as nobody, which is the 401 this whole module is about.
    config = _config(monkeypatch, {"COUCHDB_USER": "", "CDB_USER": "admin"})
    assert config.COUCHDB_USER == "admin"
