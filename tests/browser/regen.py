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

from agora_runner import nova_site, nova_sources  # noqa: E402
from agora_runner.nova_boards import BOARD_PATHS  # noqa: E402
from agora_runner.nova_comments import COMMENTS_PATH  # noqa: E402
from agora_runner.nova_costs import COST_LEDGER_PATH  # noqa: E402
from agora_runner.nova_journal import JOURNAL_PATH  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")
PAYLOAD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "payload.json")


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


def build_payload():
    """Every API response app.js loads, from the committed fixtures."""
    by_path = {
        JOURNAL_PATH: _read("journal_two_entries.md"),
        COMMENTS_PATH: _read("comments_sample.md"),
        BOARD_PATHS["issues"]["edvard"]: _read("board_sample.md"),
        BOARD_PATHS["issues"]["nova"]: _read("board_notes_sample.md"),
        # JSON rather than markdown, and the only fetch on this page the
        # other repo writes. Named explicitly because the fall-through
        # below answers the digest to anything it does not recognise, and
        # a costs page fed markdown would not fail here -- it would fail
        # in `json.loads` with a message about the digest.
        COST_LEDGER_PATH: _read("cost_ledger_sample.json"),
        # The rolled-off half of my own captures. Named explicitly for
        # the same reason the ledger is: the fall-through below answers
        # the digest to anything it does not recognise, so leaving these
        # out fed `parse_notes` the digest fixture and the archive path
        # got fixture coverage that only looked like absence because that
        # fixture happens to have no `## Entries` heading.
        BOARD_PATHS["issues"]["nova_archive"]: _read("board_notes_archive_sample.md"),
        BOARD_PATHS["ideas"]["nova_archive"]: "",
    }
    digest_md = _read("digest_two_entries.md")

    def fake_read(path):
        return by_path.get(path, digest_md)

    with patch.object(nova_sources, "vault_read_path", side_effect=fake_read), \
            patch.object(nova_sources, "vault_bulk_fetch", return_value=({}, {})):
        board = nova_site.board_payload("issues")
        journal = nova_site.journal_payload()
        return {
            # `journal_payload` is the cache's shape, not the wire's --
            # markdown kept, blocks unbuilt. What the browser gets is what
            # `journal_page` hands back for its window, so the fixture is
            # built the same way: blocks rendered, `body` left behind.
            "journal": dict(
                journal,
                entries=[nova_site._rendered(entry) for entry in journal["entries"]],
            ),
            "digest": nova_site.digest_payload(),
            "comments": nova_site.comments_payload(),
            # The two shapes /api/board answers in: the list the page
            # loads, and the one write-up a tap on a row asks for.
            "board": nova_site.board_page(board, limit=2),
            "boardItem": nova_site.board_page(board, item=57),
            "costs": nova_site.costs_payload(),
        }


if __name__ == "__main__":
    os.makedirs(os.path.dirname(PAYLOAD_PATH), exist_ok=True)
    with open(PAYLOAD_PATH, "w", encoding="utf-8") as handle:
        json.dump(build_payload(), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("wrote", PAYLOAD_PATH)
