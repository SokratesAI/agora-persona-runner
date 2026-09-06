"""`tools.seal_cert_drift` — does it see a rotated sealing key?

The fixtures are two real, self-signed certificates generated once with
openssl and pasted in, plus a third that re-wraps the SAME key as the first.
That third one is the fixture that matters: a check comparing whole
certificates passes every other test in this file and still cries wolf every
time sealed-secrets re-issues around a key it already had.
"""

import subprocess
import textwrap

import pytest

from tools import seal_cert_drift as scd


def _make_cert(tmp_path, name, key_path=None):
    """A self-signed cert; reuses `key_path`'s key when one is given."""
    key = key_path or (tmp_path / f"{name}.key")
    if key_path is None:
        subprocess.run(
            ["openssl", "genrsa", "-out", str(key), "2048"],
            check=True, capture_output=True,
        )
    crt = tmp_path / f"{name}.crt"
    subprocess.run(
        ["openssl", "req", "-new", "-x509", "-key", str(key), "-out", str(crt),
         "-days", "3650", "-subj", "/CN=" + name],
        check=True, capture_output=True,
    )
    return crt.read_text(), key


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("certs")
    march, march_key = _make_cert(tmp, "march")
    september, _ = _make_cert(tmp, "september")
    # Same key as `march`, different certificate bytes.
    reissued, _ = _make_cert(tmp, "reissued", key_path=march_key)
    assert reissued != march, "the re-issue fixture must not be byte-identical"
    return {"march": march, "september": september, "reissued": reissued}


def test_matching_key_is_clean(certs):
    status, lines = scd.report((certs["march"], None), (certs["march"], None))
    assert status == 0
    assert any("carries the key the controller is serving" in line for line in lines)


def test_rotated_key_raises(certs):
    status, lines = scd.report((certs["september"], None), (certs["march"], None))
    assert status == 2
    assert any("SEAL CERT DRIFT" in line for line in lines)
    # The remedy has to be in the output: a check that names a problem and no
    # command does not get acted on.
    assert any(scd.CONTROLLER in line for line in lines)


def test_a_reissue_around_the_same_key_is_not_drift(certs):
    """The whole reason this compares public keys instead of certificates."""
    assert certs["reissued"] != certs["march"]
    status, lines = scd.report((certs["reissued"], None), (certs["march"], None))
    assert status == 0, lines


def test_both_dates_are_printed_on_drift(certs):
    """A drift line with only one side's date cannot be acted on."""
    _, lines = scd.report((certs["september"], None), (certs["march"], None))
    body = "\n".join(lines)
    assert "committed " in body and "live " in body
    assert body.count("notBefore=") == 2
    assert body.count("serial=") == 2


@pytest.mark.parametrize(
    "live,committed",
    [
        ((None, "controller unreachable"), ("cert", None)),
        (("cert", None), (None, "gh said no")),
        ((None, "controller unreachable"), (None, "gh said no")),
    ],
)
def test_an_unreadable_side_is_never_clean(live, committed):
    status, lines = scd.report(live, committed)
    assert status == 1
    assert any("unreadable side is not a clean one" in line for line in lines)


def test_both_errors_are_printed_not_just_the_first():
    _, lines = scd.report((None, "controller unreachable"), (None, "gh said no"))
    body = "\n".join(lines)
    assert "controller unreachable" in body and "gh said no" in body


def test_garbage_is_unreadable_not_drift():
    """Two different pieces of junk are not two different keys."""
    status, lines = scd.report(("not a cert", None), ("also not a cert", None))
    assert status == 1
    assert not any("SEAL CERT DRIFT" in line for line in lines)


def test_committed_side_is_read_from_the_default_branch_not_a_checkout():
    """A local checkout can be hours behind main; the contents API cannot."""
    calls = []

    def fake_gh(args):
        calls.append(args)
        import base64, json
        return 0, json.dumps({"content": base64.b64encode(b"PEM").decode()}), ""

    pem, err = scd.fetch_committed(run=fake_gh)
    assert (pem, err) == ("PEM", None)
    assert calls == [["api", "repos/SokratesAI/platform-config/contents/"
                      "secrets/sealed-secrets-pub.pem"]]


def test_a_failing_gh_is_an_error_not_an_empty_cert():
    pem, err = scd.fetch_committed(run=lambda args: (1, "", "HTTP 404"))
    assert pem is None
    assert "HTTP 404" in err


def test_a_200_that_is_not_json_is_an_error():
    pem, err = scd.fetch_committed(run=lambda args: (0, "<html>", ""))
    assert pem is None
    assert "could not decode" in err


def test_controller_failure_is_reported_with_its_url():
    def boom(url, timeout=None):
        raise OSError("connection refused")

    pem, err = scd.fetch_live("http://example/cert.pem", open_url=boom)
    assert pem is None
    assert "http://example/cert.pem" in err and "connection refused" in err


def test_live_cert_is_returned_verbatim():
    class Response:
        def read(self):
            return b"-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    pem, err = scd.fetch_live("http://example/cert.pem", open_url=lambda u, timeout=None: Response())
    assert err is None
    assert pem == "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n"


def test_describe_is_empty_when_openssl_refuses():
    assert scd.describe("junk", run=lambda args, stdin: (1, "", "unable to load")) == ""


def test_docstring_says_what_each_exit_code_means():
    doc = textwrap.dedent(scd.__doc__)
    assert "Exit 0" in doc and "exit 2" in doc and "exit 1" in doc
