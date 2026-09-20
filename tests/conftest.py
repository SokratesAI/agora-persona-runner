"""Makes the test suite network-free by construction, not by luck.

Why this exists (2026-08-02, agora-persona-runner#29): the Evolve loop
runs `pytest` from inside the cluster, where `agora.agents.svc` actually
resolves -- and `AGORA_INTERNAL_URL` defaults to that real address
(config.py). So a test whose mock target is subtly wrong (e.g. patching
`runner.X.agora_internal` when the code under test holds its own
reference to it) does not fail. It silently makes a REAL HTTP call
against live production Agora, which cheerfully answers -- including
PATCHes against real heartbeat rows. #29 hit exactly this: 14 tests
"passed" locally while talking to production, and only failed in CI,
where there is no DNS at all.

Two lessons are encoded here. First, local green has to mean the same
thing as CI green, or it means nothing. Second, blocking DNS alone is
not enough -- that was the ad-hoc check used while debugging #29, and a
connect to a literal IP walks straight past it. So both the name lookup
and the connect itself are blocked, which is what makes this a guarantee
rather than a speed bump.

Installed via pytest_configure (not an autouse fixture) so it also
covers anything that would reach the network at import/collection time,
before the first test runs.
"""
import os
import pytest
import re
import socket
import subprocess
import sys
import threading

BLOCKED_MESSAGE = (
    "network access is blocked in tests (tests/conftest.py) -- attempted "
    "to reach {target!r}. A test should never make a real HTTP call: if "
    "you are seeing this, a mock is not patching what the code under "
    "test actually calls. Patch the reference the module itself uses."
)

_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


class NetworkBlockedInTests(RuntimeError):
    """Raised instead of opening a real connection from the test suite."""


def _blocked_getaddrinfo(host, port, *args, **kwargs):
    raise NetworkBlockedInTests(BLOCKED_MESSAGE.format(target=f"{host}:{port}"))


def _blocked_connect(self, address, *args, **kwargs):
    raise NetworkBlockedInTests(BLOCKED_MESSAGE.format(target=address))


def _blocked_connect_ex(self, address, *args, **kwargs):
    raise NetworkBlockedInTests(BLOCKED_MESSAGE.format(target=address))


def pytest_configure(config):
    socket.getaddrinfo = _blocked_getaddrinfo
    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex
    subprocess.Popen = _NoRealRemoteProcess


def pytest_unconfigure(config):
    socket.getaddrinfo = _real_getaddrinfo
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex
    subprocess.Popen = _real_popen


LEAKED_MESSAGE = (
    "this test left {count} background thread(s) still running: {names}. A "
    "thread that outlives the test has also outlived the test's patches, so "
    "whatever it does next it does against the references the patches were "
    "hiding -- and it does it while some later test is running, which is "
    "where the blame lands. Its exception, if it raises one, goes to "
    "threading's default excepthook and fails nothing. Note that the "
    "network block above is session-wide and still applies, so an HTTP "
    "call will not reach production from inside the run -- what does land "
    "is module state, files, and anything at all once the session ends and "
    "pytest_unconfigure puts the real sockets back. Either do not start the "
    "thread (patch whatever starts it) or join it before the test ends."
)

_threads_at_setup = {}


def pytest_runtest_setup(item):
    # The Thread objects rather than their `ident`s: an ident is the OS
    # thread id and the OS reuses those, so a leaked thread that happened
    # to land on a dead one's id would look like it had been here all
    # along. Holding the object costs nothing -- it does not keep the OS
    # thread alive, and the entry is dropped again in teardown.
    _threads_at_setup[item.nodeid] = set(threading.enumerate())


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Fail a test that leaves a thread running behind it.

    The network guard above is about the wrong mock target; this is about
    the right mock target and the wrong lifetime. A `with patch(...)` holds
    only until the test returns, so a background thread started inside it
    keeps running afterwards with the real thing restored underneath it --
    and it lands on whichever test happens to be running when it gets
    round to doing its work. Both repos have now paid for this shape: the
    bridge's quota watcher took its final reading past a function-scoped
    patch and appended to the real `quota-history.jsonl` (see the bridge's
    own conftest), and here the reply worker escaped a site test in about
    one run in three.

    Checked after the wrapped hook, so fixture finalizers have already run
    -- a fixture that starts a thread and joins it in teardown is doing the
    right thing and must not be flagged for it. No grace period, because
    the bug is the escape and not the duration: a thread still alive once
    the test and its fixtures are done has already outlived the patches,
    whether it finishes a millisecond later or not.
    """
    result = yield
    before = _threads_at_setup.pop(item.nodeid, None)
    if before is None:
        return result
    leaked = sorted(
        t.name for t in threading.enumerate()
        if t not in before and t.is_alive()
    )
    if leaked:
        pytest.fail(LEAKED_MESSAGE.format(count=len(leaked), names=", ".join(leaked)))
    return result


@pytest.fixture(autouse=True)
def _clear_nova_site_cache():
    """`/api/journal` and `/api/digest` are served stale-while-revalidate,
    and the cache is module state shared by every test in this process.

    The join is the other half of that: a refresh runs on its own thread,
    so a test that triggers one and returns leaves the rebuild racing the
    reset below -- it can write its payload into `_cache` *after* the
    clear, which is the exact stale-payload-into-the-next-test bug this
    fixture exists to prevent. Bounded rather than unbounded so a refresh
    that genuinely wedges is reported by the leak check above instead of
    hanging the suite; the wait is nowhere near binding, a mocked refresh
    finishes in under a millisecond.
    """
    from agora_runner.nova_site import reset_cache

    reset_cache()
    yield
    for thread in threading.enumerate():
        if thread.name.startswith("nova-site-"):
            thread.join(timeout=5)
    reset_cache()


@pytest.fixture(autouse=True)
def _no_real_demo_root(monkeypatch, tmp_path):
    """No test may sweep the real `/data/workspace/demos`.

    `tools.demo reap` moves orphan demo directories into `restore/`, and
    the reap tests hand it registries of made-up rows, so against the real
    root every live demo reads as an orphan. Measured on the first run of
    the change that added the sweep: it moved all eight directories there,
    three of them serving. Tests that need a root patch their own over this.
    """
    from tools import demo as demo_tool
    monkeypatch.setattr(demo_tool, "DURABLE_ROOT", str(tmp_path / "no-real-demos"))


@pytest.fixture(autouse=True)
def _no_ask_push_log_writes(monkeypatch):
    """No test's ask or nudge may write its push outcome to the vault.

    `needs_input.ask` and `nudge_ask.nudge` record every post they make
    (nova-kpi-push-delivered). Tests that exercise the record patch `_append`
    themselves; everything else gets a recorder that writes nowhere.
    """
    from agora_runner import ask_push_log
    monkeypatch.setattr(ask_push_log, "_append",
                        lambda path, content, after_marker: "written")


@pytest.fixture(autouse=True)
def _no_live_related_thread_lookup(monkeypatch):
    """No test's ask may list the real Agora's conversations.

    `needs_input.ask` reads every live thread before opening one (issues.md
    #234). Unpatched, a test would either reach the live store or refuse on
    an unreadable listing. Everything gets an empty listing;
    `tests/test_needs_input.py` puts its own over this.
    """
    from agora_runner import needs_input
    monkeypatch.setattr(needs_input, "agora_get",
                        lambda path: (200, {"conversations": []}))


@pytest.fixture(autouse=True)
def _no_lifecycle_writes(monkeypatch):
    """No test's `main()` may start the lifecycle ledger's vault write.

    `agora_runner.main.main()` opens with `runner_lifecycle.record("started")`
    and records again on a signal; each call starts a daemon thread that
    writes a row to the vault. A test that runs `main()` without patching
    that leaks the thread past its own patches, and the teardown hook above
    correctly fails it -- but only when the thread is still alive at
    teardown, which it usually is not. So it was patched one test file at a
    time as each one lost the race: `test_otel_tracing` and
    `test_catalog_refresh` when their files ran alone, then the drain tests
    in `test_agora_persona_runner.py`, which turned `main` red on a loaded CI
    runner at e194186 (cycle 1431) with 8847 passed and 1 error. Three files
    of the same leak is the shape, so every test gets the stub.

    `tests/test_runner_lifecycle.py` still exercises the real module: it
    calls `runner_lifecycle.record` directly, and its `main()` tests put
    their own `patch.object` over this one.

    The stub keeps the calls on `.events` rather than swallowing them, so a
    test can assert it actually intercepted something -- a stub nothing
    called would make the leak disappear for the wrong reason.
    """
    # `agora_runner/__init__.py` re-exports every public name flat, so
    # `from agora_runner import main` hands back the *function*. Reach for
    # the module, the way `tests/test_runner_lifecycle.py` already does.
    import agora_runner.main  # noqa: F401  -- import for the side effect
    runner_main = sys.modules["agora_runner.main"]

    class Ledger:
        def __init__(self):
            self.events = []

        def record(self, event, **kwargs):
            self.events.append((event, kwargs))
            return None

    ledger = Ledger()
    monkeypatch.setattr(runner_main, "runner_lifecycle", ledger)
    return ledger


@pytest.fixture
def lifecycle_events(_no_lifecycle_writes):
    """Every `runner_lifecycle` call `main()` makes, with nothing written."""
    return _no_lifecycle_writes.events


@pytest.fixture(autouse=True)
def _no_board_publisher(monkeypatch):
    """No test may start the site's board publisher (issue #203).

    `nova_site.start_nova_site` starts it, several tests drive that real
    entry point, and the publisher is a module global: once one test had
    started it, every later `invalidate("board:issues")` in the session
    queued a real draw-and-write of his board file (Cycle 1397, caught by
    the network block above). Tests of the publisher build their own
    `board_publish.Publisher` and never go through `start`.
    """
    from agora_runner import board_publish
    monkeypatch.setattr(board_publish, "start", lambda *args, **kwargs: None)
    monkeypatch.setattr(board_publish, "_publisher", None)


@pytest.fixture(autouse=True)
def _metered_day_in_memory(monkeypatch):
    """`anthropic_generate` reads and writes today's metered total in the
    vault on every turn. Give each test its own empty store, so no test
    reaches for CouchDB and one test's billed tokens never count against the
    next test's daily ceiling."""
    from agora_runner import metered_day
    store = {"content": None, "rev": None}

    def read(path):
        return store["content"], store["rev"]

    def write(path, content, if_rev=None, allow_shrink=False):
        if if_rev != store["rev"]:
            return "FAILED(409 conflict)"
        store["content"], store["rev"] = content, f"{int(store['rev'] or 0) + 1}"
        return "written"

    monkeypatch.setattr(metered_day, "vault_read_path_rev", read)
    monkeypatch.setattr(metered_day, "vault_write_path", write)
    monkeypatch.setattr(metered_day, "_local", {})
    monkeypatch.setattr(metered_day, "_local_usd", {})
    return store


SUBPROCESS_BLOCKED_MESSAGE = (
    "this test spawned a real {binary!r} process: {argv}. The network block "
    "at the top of this file patches sockets *in this interpreter*, and a "
    "subprocess has its own -- so a missing stub on a tool that shells out "
    "walks straight past it and talks to the real NAS or the real cluster "
    "during a unit run. Stub whatever the module under test calls (usually "
    "its own `_run`/`run_kubectl` helper), or give the file an autouse "
    "fixture that makes the default hermetic."
)

#: Binaries that leave this box. `git` is not here on purpose -- the suite
#: spawns it 2,641 times against temporary local repositories, which is the
#: thing under test rather than a leak. Measured 2026-09-20 over a full run.
BLOCKED_BINARIES = frozenset({"ssh", "scp", "sftp", "rsync", "kubectl"})

_real_popen = subprocess.Popen


def _blocked_binary_in(argv):
    """The blocked binary this argv would run, or None.

    Reads the argv the way the kernel does -- `argv[0]`'s basename is the
    program -- and then, only for a shell invoked with `-c`, scans the
    command string as well, because `bash -lc "kubectl get pods"` runs
    kubectl with `bash` in `argv[0]`.
    """
    if not argv:
        return None
    words = []
    for item in argv:
        words.append(os.fsdecode(item) if isinstance(item, bytes) else str(item))
    program = os.path.basename(words[0])
    if program in BLOCKED_BINARIES:
        return program
    if program in ("sh", "bash", "zsh") and any(w.startswith("-") and "c" in w.lstrip("-") for w in words[1:]):
        for blocked in sorted(BLOCKED_BINARIES):
            if re.search(rf"\b{blocked}\b", " ".join(words[1:])):
                return blocked
    return None


class _NoRealRemoteProcess(_real_popen):
    """`subprocess.Popen` that refuses to launch ssh, kubectl or a copy of them.

    Why this exists (2026-09-20): `tests/test_backup_health.py` had three
    `main` tests with no stub on either half, and on the bridge pod they
    opened a real ssh connection and ran a real `kubectl get pvc -A` every
    run. One of them then failed -- asserting exit 1 and getting 2 --
    because the live sweep had found a genuinely stale backup. The test
    failed because the tool it guards was right, and it was invisible on
    CI, where there is no NAS and no cluster to reach so both halves error
    identically. Measured before writing this: a full run spawned 50 real
    ssh/kubectl processes across 17 tests in 6 files.

    Subclassing `Popen` rather than wrapping `subprocess.run` catches every
    caller, including a module that did `from subprocess import run` before
    the patch went in -- `run`, `check_output`, `call` and `check_call` all
    build their process through the module-global `Popen`.
    """

    def __init__(self, args, *a, **kw):
        argv = [args] if isinstance(args, (str, bytes)) else list(args)
        if kw.get("shell") and argv:
            first = argv[0]
            argv = ["sh", "-c", os.fsdecode(first) if isinstance(first, bytes) else str(first)]
        blocked = _blocked_binary_in(argv)
        if blocked is not None:
            printable = " ".join(
                os.fsdecode(x) if isinstance(x, bytes) else str(x) for x in argv
            )
            raise NetworkBlockedInTests(
                SUBPROCESS_BLOCKED_MESSAGE.format(binary=blocked, argv=printable[:300])
            )
        super().__init__(args, *a, **kw)


def _pass_through_unless_default_runner(real, default_runner, hermetic, keyword):
    """A stand-in that is hermetic for production callers and real for tests.

    Several tools take their subprocess runner as a default argument bound at
    import (`run=subprocess.run`, `run=_kubectl`) and hand it straight down,
    so patching `subprocess.run` afterwards never reaches them. That makes
    "did anybody stub this?" answerable in one way only: look at the runner
    that arrived. If it is still the module's own default, nobody did, and
    `hermetic` is returned instead of launching a real process. If it is
    anything else, a test built it and gets the real function.

    `real` is captured by the caller before the replacement goes in, so the
    pass-through path is the genuine reader and not this wrapper again.
    """
    def stand_in(*args, **kwargs):
        runner = kwargs.get(keyword, default_runner)
        if runner is not default_runner:
            return real(*args, **kwargs)
        for arg in args:
            if callable(arg) and arg is not default_runner:
                return real(*args, **kwargs)
        return hermetic
    return stand_in
