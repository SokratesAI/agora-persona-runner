"""http_json asks for gzip and decodes it.

The interesting half of this change is what urllib does, not what our
own code does: urllib sends no `Accept-Encoding` of its own and never
decodes one, so a test that patches `urlopen` would be asserting my
beliefs about urllib rather than urllib's behaviour.

A loopback server is not an option -- tests/conftest.py blocks
`socket.connect` outright and deliberately. So these install a real
`urllib` opener that answers from memory. Everything above the socket is
genuine: the real `Request`, urllib's own header assembly and
capitalisation, the real `HTTPErrorProcessor` turning a 503 into an
`HTTPError`, and the real `_decoded_body`. Only the wire is missing, and
the wire is not what changed.
"""

import email.message
import gzip
import io
import json
import urllib.request
import urllib.response

import pytest

from agora_runner.http_util import http_json


def _headers(pairs):
    message = email.message.Message()
    for key, value in pairs.items():
        message[key] = value
    return message


class _CannedHandler(urllib.request.HTTPHandler):
    """Answers any http:// request from memory, recording what was sent.

    Subclasses HTTPHandler rather than BaseHandler so `build_opener`
    *replaces* the real one -- a sibling handler with the same
    `handler_order` would leave urllib free to pick the socket-backed
    one, which conftest then blocks.
    """

    def __init__(self, compress=True, status=200):
        self.compress = compress
        self.status = status
        self.seen = None

    def http_open(self, req):
        self.seen = req
        body = json.dumps({
            "accept_encoding": req.get_header("Accept-encoding"),
            "content_type": req.get_header("Content-type"),
            "payload": "x" * 4000,
        }).encode()
        headers = {"Content-Type": "application/json"}
        if self.compress:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        response = urllib.response.addinfourl(
            io.BytesIO(body), _headers(headers), req.full_url, self.status)
        # urllib's HTTPErrorProcessor reads `.msg` (the reason phrase) off
        # every response on its way out; addinfourl delegates unknown
        # attributes to the underlying file, which has no such thing.
        response.msg = "OK" if self.status < 400 else "Service Unavailable"
        return response


@pytest.fixture
def serve():
    """Installs a canned opener for the duration of one test."""
    previous = urllib.request._opener

    def install(compress=True, status=200):
        handler = _CannedHandler(compress=compress, status=status)
        urllib.request.install_opener(urllib.request.build_opener(handler))
        return handler

    yield install
    urllib.request.install_opener(previous)


def test_asks_for_gzip(serve):
    serve()
    status, body = http_json("GET", "http://agora.test/conversations")
    assert status == 200
    assert body["accept_encoding"] == "gzip"


def test_decodes_a_gzipped_response(serve):
    serve(compress=True)
    status, body = http_json("GET", "http://agora.test/conversations")
    assert status == 200
    assert body["payload"] == "x" * 4000


def test_still_reads_an_uncompressed_response(serve):
    """A server that ignores the header has to keep working: Agora's
    internal port (8081) does not compress, and only the public one does."""
    serve(compress=False)
    status, body = http_json("GET", "http://agora.test/heartbeats")
    assert status == 200
    assert body["payload"] == "x" * 4000


def test_decodes_a_gzipped_error_body(serve):
    """The HTTPError path reads the body through a separate call and would
    otherwise hand gzip bytes to json.loads, swallow the exception and
    return {} -- turning a readable error into an empty dict, which
    poll_once logs as a bare status code."""
    serve(compress=True, status=503)
    status, body = http_json("GET", "http://agora.test/conversations")
    assert status == 503
    assert body["payload"] == "x" * 4000


def test_caller_headers_survive(serve):
    """agora_internal's x-agora-token and the JSON content type must not be
    displaced by adding a default header."""
    handler = serve()
    status, body = http_json("GET", "http://agora.test/heartbeats",
                             headers={"x-agora-token": "t"})
    assert status == 200
    assert body["accept_encoding"] == "gzip"
    assert body["content_type"] == "application/json"
    assert handler.seen.get_header("X-agora-token") == "t"


def test_caller_can_opt_out(serve):
    serve(compress=False)
    status, body = http_json("GET", "http://agora.test/conversations",
                             headers={"Accept-Encoding": "identity"})
    assert status == 200
    assert body["accept_encoding"] == "identity"
