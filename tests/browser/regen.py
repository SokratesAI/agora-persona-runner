"""Regenerate the payload the browser tests drive `app.js` with.

    python3 tests/browser/regen.py

The browser tests run under node and cannot import the Python that builds
the real response, so the contract between the two halves is a committed
JSON file. That file is only trustworthy if something proves it still
matches what the server actually sends, which is what
`test_the_browser_fixture_is_what_the_server_would_send` does in
tests/test_nova_site.py -- it rebuilds this and compares, and fails
pointing back here. So the fixture cannot silently drift away from the
server the way a hand-written mock would.

It is built by calling `journal_payload()` and `digest_payload()`
themselves, with only the vault read replaced, rather than by
reassembling their parts here. Anything those functions do to the payload
on the way out is therefore in the fixture too.
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from agora_runner import nova_site  # noqa: E402
from agora_runner.nova_journal import JOURNAL_PATH  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")
PAYLOAD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "payload.json")


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


def build_payload():
    """The two API responses app.js loads, from the two-entry fixtures."""
    journal_md = _read("journal_two_entries.md")
    digest_md = _read("digest_two_entries.md")

    def fake_read(path):
        return journal_md if path == JOURNAL_PATH else digest_md

    with patch.object(nova_site, "vault_read_path", side_effect=fake_read):
        return {"journal": nova_site.journal_payload(), "digest": nova_site.digest_payload()}


if __name__ == "__main__":
    os.makedirs(os.path.dirname(PAYLOAD_PATH), exist_ok=True)
    with open(PAYLOAD_PATH, "w", encoding="utf-8") as handle:
        json.dump(build_payload(), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("wrote", PAYLOAD_PATH)
