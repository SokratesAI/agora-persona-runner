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


@pytest.fixture(autouse=True)
def _clear_nova_site_cache():
    """`/api/journal` and `/api/digest` are served stale-while-revalidate,
    and the cache is module state shared by every test in this process."""
    from agora_runner.nova_site import reset_cache

    reset_cache()
    yield
    reset_cache()
