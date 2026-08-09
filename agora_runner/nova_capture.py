"""The capture box: one field on Nova's site, one bullet in Edvard's backlog.

Idea #34 item 6, and the first thing on this site that *writes* to the
vault. It replaces opening Obsidian on a phone to type one line.

**Where a capture lands, and why exactly there.** Both target files
declare their own contract in frontmatter -- *"Edvard writes in the bare
bullet list at the top ... Nova numbers it, boards it, and always leaves
exactly one empty bullet there so he can start typing immediately"*. A
capture is Edvard writing, so it goes in that list and nowhere else, with
no timestamp, no "via web" marker and no provenance tag. It has to be
indistinguishable from the same line typed in Obsidian, because it *is*
the same line by the same author, and prompt.md treats a bare bullet at
the top as the strongest signal a cycle gets. Annotating it would be me
putting my own text in a file that is his.

The two files disagree about the blank line after the frontmatter
(`issues.md` has one, `ideas.md` does not), so the list is found
structurally -- frontmatter, then the run of top-level bullets before the
first heading -- never by offset.

**The write is a read-modify-write against a live vault, and it can
lose.** `vault_write_path` sends CouchDB the `_rev` it just read, so a
concurrent edit -- a cycle boarding these very files, or LiveSync
flushing the phone -- makes the PUT fail with 409 rather than silently
clobbering. That is the good case, and it is why the retry below re-reads
before each attempt instead of resending. Any non-409 failure is not a
conflict and retrying it would just spin.

**One limit, one measured danger.** The runner pod's memory limit is
256Mi (measured live 2026-08-09), and `rfile.read(n)` allocates whatever
`Content-Length` claims, so an unbounded body is a sized memory hazard on
a real ceiling. That is what MAX_BODY_BYTES defends, and it is the only
number here. There is deliberately no separate cap on the text itself,
no rate limit and no truncation: those would be limits without a danger
I have measured, and a capture that arrives clipped is worse than no
capture at all.
"""

from agora_runner.log import log
from agora_runner.vault import vault_read_path, vault_write_path

CAPTURE_TARGETS = {
    "issues": "projects/sokrates/projects/agora/issues.md",
    "ideas": "projects/sokrates/projects/agora/ideas.md",
}

# 64 KiB. A capture is a line typed on a phone; this is orders of
# magnitude above any real one and still bounded against the 256Mi limit.
MAX_BODY_BYTES = 64 * 1024

WRITE_ATTEMPTS = 3


def _frontmatter_end(lines):
    """Index of the first line *after* the closing `---`, or 0."""
    if not lines or lines[0].strip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i + 1
    return 0


def clean_capture_text(text):
    """Text as typed -> the bullets to add.

    Each non-blank line becomes its own bullet. A multi-line paste into a
    one-line-per-item file is far more likely to be several captures than
    one wrapped thought -- and the deciding argument is structural rather
    than a guess about intent: a bullet containing a raw newline would
    break the list it lives in.

    A leading `- ` is stripped so typing the bullet character yields one
    bullet rather than `- - like this`.
    """
    bullets = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        elif line == "-":
            line = ""
        if line:
            bullets.append(line)
    return bullets


def insert_captures(markdown, bullets):
    """Add `bullets` to the capture list, keeping exactly one empty bullet last.

    The empty bullet is the cursor Edvard types into, so it stays at the
    bottom of the list and captures accumulate above it in the order they
    were written. If the file has lost its empty bullet, this restores it
    -- that is the file's own documented contract, not invented structure.
    """
    if not bullets:
        return markdown
    lines = markdown.split("\n")
    start = _frontmatter_end(lines)

    # The capture list is the run of top-level bullets between the
    # frontmatter and the first heading. Scanning stops at the heading so
    # a bullet inside the Board or Details sections can never be mistaken
    # for it.
    first = None
    end = start
    i = start
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            break
        if stripped == "-" or stripped.startswith("- "):
            if first is None:
                first = i
            end = i + 1
        i += 1

    if first is None:
        # No capture list at all. Put one where the contract says it goes,
        # rather than dropping the capture or appending it somewhere the
        # next cycle would not look.
        block = [""] + [f"- {b}" for b in bullets] + ["- ", ""]
        return "\n".join(lines[:start] + block + lines[start:])

    # Everything from the frontmatter down to the first bullet is kept
    # verbatim: `issues.md` has a blank line there and `ideas.md` does not,
    # and normalising them to match would be me quietly reformatting a file
    # that is his. Only the empty bullet is removed, because exactly one is
    # re-added at the end of the list below.
    lead = lines[start:first]
    existing = [line for line in lines[first:end] if line.strip() != "-"]
    block = lead + existing + [f"- {b}" for b in bullets] + ["- "]
    return "\n".join(lines[:start] + block + lines[end:])


def capture(target, text):
    """Append a capture to `issues.md` or `ideas.md`. Returns (ok, message).

    `target` is a key into CAPTURE_TARGETS, never a path -- nothing a
    client sends is ever used to address a vault document.
    """
    path = CAPTURE_TARGETS.get(target)
    if path is None:
        return False, f"unknown target: {target!r}"
    bullets = clean_capture_text(text or "")
    if not bullets:
        return False, "nothing to capture"

    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current = vault_read_path(path)
        if current is None:
            return False, f"{path} not found"
        result = vault_write_path(path, insert_captures(current, bullets))
        if result == "written":
            log(f"nova-capture wrote {len(bullets)} bullet(s) to {target}")
            return True, f"captured to {target}"
        # 409 is the conflict this design expects: someone else wrote
        # between the read and the PUT, so re-read and rebuild. Anything
        # else is not a conflict and will fail identically next time.
        if "409" not in result:
            break
    log(f"nova-capture failed writing to {target}: {result}")
    return False, f"could not write to {target}: {result}"
