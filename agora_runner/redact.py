"""Strips live credentials out of anything this process publishes to Agora.

The same problem, and the same answer, as `bridge/redact.py` in
agora-claude-bridge -- deliberately a second copy rather than a shared
package, because the two run in different pods with no common dependency
and a filter that can be skipped by a deploy skew is not a filter.

That one covers what a claude-cli persona's tools return, because those
run inside the bridge. It does not cover this process, and this process
has its own, larger unfiltered path: every other provider runs its tools
here, on this stack, and audit() publishes what they touched. Two of
those carry raw material nobody reviewed --

  * `terminal_exec` audits the command verbatim, so a `curl -H
    "Authorization: Bearer ..."` puts the header in the feed;
  * `vault_write` audits the whole file as before/after so Agora can
    render a diff, so any vault note holding a token publishes it.

-- and a token has already reached a conversation once this way (Cycle
20, an OAuth access *and* refresh token read out of the CLI's own
credentials file).

Same narrow contract as the bridge's: every pattern below matches a
credential *format*, not a topic, and a hit is replaced with a visible
`[redacted: <what>]` rather than silently dropped. Edvard's standing rule
is that nothing is thrown away to make the UI tidier -- the answer to
"too much output" is an interface, not a filter. A live credential is the
one exception, because the danger is nameable and it has happened.
"""

import re

# (label, pattern). The label is what the reader sees in place of the
# secret, so it says what was removed without saying what it was.
_PATTERNS = (
    # Anthropic API keys and, the ones that actually leaked, the OAuth
    # access/refresh tokens the CLI stores (sk-ant-oat01-/sk-ant-ort01-).
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    # GitHub: PATs (classic + fine-grained), OAuth, user, server, refresh.
    ("github token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}")),
    ("github token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    # A JWT is three base64url segments; the middle one starts a JSON
    # object, so a real one begins eyJ. Session cookies and k8s
    # ServiceAccount tokens both take this shape.
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("aws key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    )),
    # `printenv`, a .env file, a k8s secret dumped as YAML/JSON: the value
    # is unguessable but the NAME beside it is not. Only the value is
    # replaced -- the name stays, because knowing that ANTHROPIC_API_KEY is
    # set is exactly the kind of thing he wants to be able to see.
    #
    # Two of those three shapes were missed until Cycle 170, and the drift
    # probes in tools/sync_contract.py found both on their first run:
    #
    #   * JSON quotes the NAME too, so `"couchdb_password": "x"` put a `"`
    #     between the name and the colon and nothing matched. The optional
    #     quote stays inside group 2, so the replacement puts it back and the
    #     document is still parseable.
    #   * `_PASS` is not `PASSWD` or `PASSWORD`, and `CDB_PASS` is the name
    #     this very system holds its CouchDB password under. It is spelled
    #     `_PASS` rather than `PASS` on purpose: a bare `pass:` is an
    #     ordinary English word, and "second pass: completed" is exactly the
    #     over-redaction Edvard's keep-everything rule forbids.
    ("value", re.compile(
        r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|_PASS|API[_-]?KEY|ACCESS[_-]?KEY|CREDENTIAL)S?)"
        r"(\"?\s*[=:]\s*\"?)"
        # The lookahead is load-bearing and was not in the pre-Cycle-170
        # version, because that version could not reach this position at all
        # in JSON. `_PATTERNS` runs in order, so by the time this one sees
        # the CLI credentials file the anthropic-key pattern has already
        # replaced the token with `[redacted: anthropic key]` -- and
        # `[redacted:` is 10 characters none of which are excluded below, so
        # this pattern matched the marker as if it were the value and
        # produced `[redacted: value] anthropic key]`. Caught by
        # test_tool_output_is_redacted_on_the_way_out in the bridge, which
        # had been green for weeks and went red on the widening.
        r"((?!\[redacted:)[^\s\"',}]{8,})"
    )),
)


def redact(text):
    """`text` with any credential-shaped run replaced by a visible marker.

    Returns non-strings unchanged so callers don't have to type-check
    before handing over whatever a tool returned.
    """
    if not isinstance(text, str) or not text:
        return text
    for label, pattern in _PATTERNS:
        if label == "value":
            text = pattern.sub(rf"\1\2[redacted: {label}]", text)
        else:
            text = pattern.sub(f"[redacted: {label}]", text)
    return text
