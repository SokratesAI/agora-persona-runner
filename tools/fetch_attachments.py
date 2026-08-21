"""Pull the images out of a capture file and onto local disk, so a cycle can look at them.

Edvard, comments board 2026-08-21 15:51 and again at 16:06, sent me a
screenshot through the attach button Cycle 302 had just built for him. Both
times the cycle reading the board answered that it could not see it —
*"Still nothing came through on my end — I can't see images in this chat, so
whatever's in that screenshot, I'm blind to it here."*

That was wrong, and it was wrong in the specific way this loop's own
`prompt.md` spends four paragraphs warning about: **"I can't" is a
measurement, not a conclusion.** Nothing was broken. `store_upload` had put
the bytes in the vault, the site was serving them at `/api/upload/<name>`,
and the cycle had a tool that renders an image from local disk. What was
missing was the one step between a markdown link in `comments.md` and a
file on disk — nobody had written it, so every cycle read
`![1000031053.jpg](/api/upload/89f92e….jpg)` as a dead string and reported
a capability gap instead of spending a shell call. Cycle 304 spent the
shell call: 336,336 bytes, HTTP 200, and the screenshot was a bug report
about the app showing `CAN'T REACH NOVA`.

So this tool is deliberately dumb. It finds every `/api/upload/<name>` in a
file, fetches each through `/app/bridge/vault_tool.py` (the bridge pod's
vault client — the runner-side `read_upload` needs `COUCHDB_*`, which the
bridge pod does not have), decodes it with the *same* decoder that stored
it, writes it to a directory, and prints the paths. Reading them is the
cycle's job, and it is one call per image.

**It reports the heading each attachment sits under**, because an image
with no idea which comment it belongs to is barely better than no image.

Usage, from the bridge pod (`Bash`), after fetching the file you are about
to read anyway:

    python3 /app/bridge/vault_tool.py get \
      'projects/sokrates/projects/agora/nova/resources/comments.md' > /tmp/comments.md
    python3 -m tools.fetch_attachments /tmp/comments.md

Exit 0 with `no attachments` when the file has none, which is the common
case and costs nothing. Exit 1 only when an attachment was named and could
not be fetched — a link that resolves to nothing is a real defect and the
whole point is that it stops being invisible.
"""

import argparse
import os
import re
import subprocess
import sys

from agora_runner.nova_uploads import UPLOAD_PREFIX, decode_envelope, is_upload_name

VAULT_TOOL = "/app/bridge/vault_tool.py"

#: Where fetched attachments land. `/data/workspace` persists across cycles
#: and `tools.tidy_workspace` archives what is left in it, so an image a
#: cycle looked at is cleaned up by the machinery that already exists.
DEFAULT_DIR = "/data/workspace/attachments"

#: The shape `store_upload` puts into Edvard's files: `/api/upload/<32 hex>.<ext>`.
#: Matched loosely and validated with `is_upload_name`, so a near-miss is
#: reported rather than silently skipped.
LINK = re.compile(r"/api/upload/([A-Za-z0-9._-]+)")

HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")


def find_links(text):
    """`[(name, heading)]` in document order, first occurrence of each name.

    `heading` is the nearest markdown heading above the link — in
    `comments.md` that is the `### Cycle <n> · <stamp>` the comment was
    filed under, which is how a cycle knows what it is looking at.
    """
    found = []
    seen = set()
    heading = ""
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            heading = match.group(1)
            continue
        for name in LINK.findall(line):
            if name not in seen:
                seen.add(name)
                found.append((name, heading))
    return found


def _vault_get(path, vault_tool):
    proc = subprocess.run(
        [sys.executable, vault_tool, "get", path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    body = proc.stdout
    # `vault_tool.py get` prints `[not found: ...]` and exits 0 for a path
    # that does not exist -- measured, and `tools.append_retro` documents
    # the same behaviour. Treat it as absent rather than as an envelope.
    if body.startswith("[not found"):
        return None
    return body


def fetch(text, out_dir, vault_tool=VAULT_TOOL, getter=None):
    """Write every attachment in `text` to `out_dir`.

    Returns `[(name, heading, path_or_None, detail)]` in document order.
    `path_or_None` is `None` for anything that could not be fetched, and
    `detail` says why, or gives `content-type, bytes` when it worked.
    """
    getter = getter or (lambda path: _vault_get(path, vault_tool))
    results = []
    for name, heading in find_links(text):
        if not is_upload_name(name):
            results.append((name, heading, None, "not an upload name"))
            continue
        body = getter(UPLOAD_PREFIX + name)
        if body is None:
            results.append((name, heading, None, "not in the vault"))
            continue
        decoded = decode_envelope(body)
        if decoded is None:
            results.append((name, heading, None, "envelope did not decode"))
            continue
        content_type, raw = decoded
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, name)
        with open(path, "wb") as handle:
            handle.write(raw)
        results.append((name, heading, path, f"{content_type}, {len(raw)} bytes"))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", help="a capture file already fetched to local disk")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="where to write the images")
    parser.add_argument("--vault-tool", default=VAULT_TOOL)
    args = parser.parse_args(argv)

    with open(args.file, encoding="utf-8") as handle:
        text = handle.read()

    results = fetch(text, args.dir, vault_tool=args.vault_tool)
    if not results:
        print("no attachments")
        return 0

    failed = 0
    for name, heading, path, detail in results:
        where = f" under {heading}" if heading else ""
        if path is None:
            failed += 1
            print(f"FAILED {name}{where}: {detail}")
        else:
            print(f"{path} ({detail}){where}")
    if failed:
        print(f"\n{failed} of {len(results)} could not be fetched", file=sys.stderr)
        return 1
    print(f"\nRead these {len(results)} file(s) -- they are images Edvard sent you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
