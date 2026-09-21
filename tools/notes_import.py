"""Copy every note in `notes.md` into the notes store, once (idea #333).

The new `/notes` page reads records, and until this ran the store held none
of the notes he had already left -- so switching the page over would have
shown him an empty list with his standing instructions and Sokrates' outage
report gone from it. This is the copy that has to land before that switch.

Each note keeps the text the current page shows (`nova_notes.parse_notes_page`,
so a wrapped line is joined exactly as he sees it today) and gets an id
derived from that text: `note:md<sha1[:10]>`. Running it twice writes nothing
the second time, so it can run again at the cutover to pick up whatever he
typed into `notes.md` in between. A reply a cycle wrote under a note becomes a
comment from Nova on it.

**Nothing here is a real date.** `notes.md` never recorded when a note was
written, so `created` is the time of the import and the file's own order is
kept in `imported.position` (0 = top of the file = newest). The author is
`sokrates` for a note that opens "Sokrates here" or names the owner in the third
person, and `edvard` for the rest (`author_of` says why).

    python3 -m tools.notes_import            # dry run: what would be written
    python3 -m tools.notes_import --write

Exit 1 when the vault read or a store write failed.
"""

import argparse
import hashlib
import re
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import nova_notes, nova_notes_store as store

NOTES_PATH = "projects/sokrates/projects/nova/notes.md"


def author_of(text):
    """`edvard` only for a note he could have typed himself.

    A note that opens "Sokrates here" is Sokrates' signature. A note that
    names the owner in the third person ("Standing instruction from <his
    name>, ...") was written *about* him, not by him, and the only other writer of
    this file is Sokrates -- so it is his too. Signing it `edvard` would put
    words under his name he did not type.
    """
    if text.lstrip("- ").lower().startswith("sokrates here") or "edvard" in text.lower():
        return "sokrates"
    return "edvard"


def note_id(text):
    return "note:md" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _blocks(body):
    """A section's text -> its blank-line-separated blocks, in file order."""
    return [block for block in re.split(r"\n\s*\n", body or "") if block.strip()]


def plan(markdown):
    """`notes.md` -> the records it becomes, top of the file first.

    **One blank-line block is one note.** Sokrates' outage report is six
    bullets with no blank line between them; read bullet by bullet it became
    six notes and five of them were signed as the owner, because only the first
    says "Sokrates here". A block's bullets are kept as a markdown list, one
    per line, and the block takes the author of its first line.
    """
    text = nova_notes._strip_frontmatter(markdown)
    heading = re.search(r"^#{1,6} ", text, re.M)
    sections = [("waiting", text[: heading.start()] if heading else text)]
    read = re.search(r"^## Read[ \t]*$", text, re.M)
    if read:
        after = text[read.end():]
        end = re.search(r"^#", after, re.M)
        sections.append(("read", after[: end.start()] if end else after))
    records = []
    for section, body in sections:
        for block in _blocks(body):
            notes = nova_notes._bullets(block)
            if not notes:
                continue
            records.append({"section": section,
                            "text": "\n".join(n["text"] for n in notes) if len(notes) == 1
                                    else "\n".join("- " + n["text"] for n in notes),
                            "responses": [r for n in notes for r in n["responses"]]})
    return records


def write(records, now=None):
    """Create the records the store does not hold yet; return (written, skipped)."""
    now = now or store._now()
    written = skipped = 0
    for position, record in enumerate(records):
        nid = note_id(record["text"])
        doc = {"_id": nid, "type": store.NOTE_TYPE, "author": author_of(record["text"]),
               "text": record["text"], "archived": False, "created": now, "updated": now,
               "imported": {"from": NOTES_PATH, "section": record["section"], "position": position}}
        if store._create(doc) is None:
            skipped += 1
        else:
            written += 1
        key = nid[5:]
        for number, response in enumerate(record["responses"], 1):
            store._create({"_id": f"comment:{key}:{number:06d}", "type": store.COMMENT_TYPE,
                           "noteId": nid, "author": "nova", "text": response, "created": now})
    return written, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write the records; without it, only print them")
    args = parser.parse_args(argv)
    got = subprocess.run([sys.executable, "/app/bridge/vault_tool.py", "get", NOTES_PATH],
                         capture_output=True, text=True)
    if got.returncode != 0 or not got.stdout.startswith("---"):
        print(f"could not read {NOTES_PATH}: {got.stderr.strip() or got.stdout[:200]}")
        return 1
    records = plan(got.stdout)
    for position, record in enumerate(records):
        print(f"{position:3d} {record['section']:7s} {author_of(record['text']):8s} "
              f"{note_id(record['text'])} +{len(record['responses'])} reply  {record['text'][:70]}")
    if not args.write:
        print(f"{len(records)} note(s); dry run, nothing written (pass --write)")
        return 0
    try:
        written, skipped = write(records)
    except store.StoreError as e:
        print(f"store refused a write: {e}")
        return 1
    print(f"{written} written, {skipped} already in the store")
    return 0


if __name__ == "__main__":
    sys.exit(main())
