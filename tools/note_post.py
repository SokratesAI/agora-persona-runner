"""Post a note, or a comment on one, as Sokrates or Nova, straight into the notes store.

The writer half of `notes-records.md` for the two senders who are not
Edvard. Measured Cycle 1997 and written into the spec: Sokrates does not
reach the Nova site at all. He runs on the host, with the CouchDB admin
credential over the NodePort, and has always written by putting text into
vault files. A per-sender token on the site's port would guard a door he
never uses, so he gets this instead -- the same store call the site's
routes will make, with the sender named on the command line.

`--as edvard` is refused on purpose. Edvard's notes come only from his own
route, where his login is read off the Tailscale proxy; a command anyone on
the box can type is not that.

    python3 -m tools.note_post --as sokrates --text 'server2 is back'
    echo 'multi-line\n\ntext' | python3 -m tools.note_post --as nova --text -
    python3 -m tools.note_post --as sokrates --on note:8f2a1c --text 'seen'

Prints the stored record's id. Exit 1 when the store refused the write,
2 on a usage error.
"""

import argparse
import sys

from agora_runner import nova_notes_store as store

SENDERS = ("sokrates", "nova")


def post(sender, text, on=None):
    """Write the note (or the comment on `on`) and return the stored record."""
    if sender not in SENDERS:
        raise ValueError(f"--as must be one of {', '.join(SENDERS)}, not {sender!r}")
    if on:
        return store.add_comment(on, sender, text)
    return store.create_note(sender, text)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m tools.note_post", description=__doc__.splitlines()[0])
    parser.add_argument("--as", dest="sender", required=True, choices=SENDERS)
    parser.add_argument("--text", required=True, help="the text, or - to read it from stdin")
    parser.add_argument("--on", help="a note id (note:<hex>) to comment on instead of posting a note")
    args = parser.parse_args(argv)
    text = sys.stdin.read() if args.text == "-" else args.text
    try:
        record = post(args.sender, text, args.on)
    except (ValueError, store.StoreError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(record["_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
