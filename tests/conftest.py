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
import pytest
import socket
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


def pytest_unconfigure(config):
    socket.getaddrinfo = _real_getaddrinfo
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex


LEAKED_MESSAGE = (
    "this test left {count} background thread(s) still running: {names}. A "
    "thread that outlives the test has also outlived the test's patches, so "
    "whatever it does next it does against the real vault, the real bridge "
    "and the real clock -- and it does it while some later test is running, "
    "which is where the blame lands. Either do not start the thread (patch "
    "whatever starts it) or join it before the test ends."
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
