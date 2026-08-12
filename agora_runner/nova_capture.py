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
lose.** The read hands back the `_rev` it saw and the write sends that
same `_rev`, so a concurrent edit -- a cycle boarding these very files,
or LiveSync flushing the phone -- makes the PUT fail with 409 rather than
silently clobbering. That is the good case, and it is why the retry below
re-reads before each attempt instead of resending. Any non-409 failure is
not a conflict and retrying it would just spin.

This paragraph described the wrong mechanism until 2026-08-12, and it was
wrong in the direction that matters: `vault_write_path` looked up a
*fresh* revision immediately before the PUT, so the other writer's edit
was adopted and overwritten and the 409 the retry loop below is built
around could not occur. The loop was real; the conflict it waited for was
not. Passing `if_rev` is what makes the sentence true.

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
from agora_runner.vault import vault_read_path_rev, vault_write_path

# These three moved out of `projects/sokrates/projects/agora/` on
# 2026-08-12. Edvard had asked whether they should follow Nova into its
# own database; the answer was no, and the reason is worth keeping:
# *"It is actually a good point to leave them in my Vault just in case
# the Nova app malfunctions or something else goes wrong. Then I have
# easy access to them. But they can be moved into the Nova folder in my
# Vault and not be underneath the agora project folder."* So they stay in
# `obsidian` -- his database, and therefore on his phone -- and only the
# folder changed.
#
# `projects/sokrates/projects/nova/` is deliberately NOT the same folder
# as `projects/sokrates/projects/agora/nova/`, which routes to Nova's own
# database. One is his; one is Nova's; they differ by a path segment.
# `test_vault_database_routing` pins these three to `obsidian` for that
# reason -- adding this prefix to `NOVA_DB_FOLDERS` because it says
# "nova" would take the only three files he writes by hand off his phone.
CAPTURE_TARGETS = {
    "issues": "projects/sokrates/projects/nova/issues.md",
    "ideas": "projects/sokrates/projects/nova/ideas.md",
    # Edvard, issues.md 2026-08-12: *"I should be able to just leave you
    # notes instead of just issues and ideas. I have said this 2-3 times
    # before. Add a button next to issues/ideas in the Nova app that lets
    # me just send you notes."* A note is neither a bug nor a proposal --
    # it is context, a correction, a preference, something he wants a
    # cycle to know. Forcing it into one of the other two files is what
    # made him ask three times.
    #
    # `notes.md` deliberately carries the same bare-bullet contract as the
    # other two rather than a shape of its own, because every line of this
    # module is about *that* list and a third convention would need a
    # third parser. What differs is downstream: notes are never boarded,
    # numbered or given a `# Details` block. A cycle reads them and acts;
    # `prompt.md` step 1a is where that obligation is written down, and
    # without it this button files into a file nothing opens.
    "notes": "projects/sokrates/projects/nova/notes.md",
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


def _capture_span(lines):
    """`(start, first, end)` for the capture list, or `(start, None, start)`.

    The capture list is the run of top-level bullets between the
    frontmatter and the first heading. Scanning stops at the heading so a
    bullet inside the Board or Details sections can never be mistaken for
    it. `start` is where the frontmatter ends, `first` the line the list
    begins on, `end` one past its last bullet.

    Shared by all three writers rather than repeated, because "which lines
    are the capture list" is the one judgement they must agree on: an edit
    that scanned a wider region than the insert could rewrite a Board row.
    """
    start = _frontmatter_end(lines)
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
    return start, first, end


def capture_entries(markdown):
    """`[(start_line, end_line, text)]` for the capture list, in file order.

    **`text` is exactly what `nova_boards._captures` puts on the page**,
    joining rule included: a continuation line is folded into the bullet
    above it, because the same files are edited in Obsidian on a phone and
    a capture that wrapped would otherwise show Edvard half a sentence.
    That joining is why this returns a *span* rather than a line number --
    one capture can be two lines in the file, and replacing only the first
    would leave its second half orphaned as a stray paragraph.

    The two functions must agree about what a capture is, and the first
    version of this file got that exactly backwards: it deliberately did
    *not* join, reasoning that a joined address would match no single line
    and so fail safely. It does fail -- but the page is what supplies the
    address, and the page joins, so Edit and Delete were permanently dead
    on any wrapped capture and said "no longer in the list", which is
    false. Found by review, reproduced against the live parser.
    """
    lines = (markdown or "").split("\n")
    start = _frontmatter_end(lines)
    entries = []
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith("- ") and stripped[2:].strip():
            entries.append((i, i + 1, stripped[2:].strip()))
        elif stripped and entries and not stripped.startswith(("-", "*", "|")):
            begin, _, text = entries[-1]
            entries[-1] = (begin, i + 1, text + " " + stripped)
    return entries


def list_captures(markdown):
    """The capture list's texts, exactly as the board page shows them."""
    return [text for _, _, text in capture_entries(markdown)]


def replace_capture(markdown, index, original, bullets):
    """Swap capture `index` for `bullets`, if it still reads `original`.

    **Both halves of the address are load-bearing, and each covers a way
    the other fails.** The index alone is not enough: these files are
    rewritten by cycles constantly, so a position taken from a page
    painted a minute ago points at a different capture the moment anything
    above it is boarded, and the edit would land on the wrong line with
    nothing to say so. The text alone is not enough either -- two captures
    can read the same, and matching on text would find the first one,
    rewrite it, and report success, which is the same wrong-line edit
    dressed as a working feature. That second hole was in the first
    version of this function and was found by review, not by me.

    So: the index says which one, the text says it has not moved, and a
    disagreement is refused rather than resolved. `None` means "not there
    any more", which is a different answer to Edvard than a failed write
    and only one of them is an error.

    Passing no bullets deletes it. The empty cursor bullet is not a
    capture and is never addressable -- it is the file's contract.
    """
    entries = capture_entries(markdown)
    wanted = (original or "").strip()
    if not wanted or not isinstance(index, int) or not 0 <= index < len(entries):
        return None
    begin, end, text = entries[index]
    if text != wanted:
        return None
    lines = (markdown or "").split("\n")
    return "\n".join(lines[:begin] + [f"- {b}" for b in bullets] + lines[end:])


def amend(target, index, original, text):
    """Edit or delete one capture. Empty `text` deletes. Returns (ok, message).

    Issues #66: *"The reported issues should be able to be edited and
    deleted by me."* Same read-modify-write and same 409 retry as
    `capture`, for the same reason -- and the retry matters more here,
    because a cycle boarding these files is exactly the concurrent writer
    that would collide.

    The re-read inside the loop is not just about the conflict. If the
    losing attempt's re-read no longer contains the bullet, the capture
    was boarded or removed between the attempts, and `replace_capture`
    returns `None` rather than resurrecting it.
    """
    path = CAPTURE_TARGETS.get(target)
    if path is None:
        return False, f"unknown target: {target!r}"
    if not (original or "").strip():
        return False, "nothing to amend"
    bullets = clean_capture_text(text or "")

    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(path)
        if current is None:
            return False, f"{path} not found"
        amended = replace_capture(current, index, original, bullets)
        if amended is None:
            # Not a write failure: the bullet is not there to amend. Most
            # likely a cycle boarded it while this page was open, which is
            # the ordinary outcome rather than a fault.
            return False, "that capture is no longer in the list"
        result = vault_write_path(path, amended, if_rev=rev)
        if result == "written":
            what = "edited" if bullets else "deleted"
            log(f"nova-capture {what} a capture in {target}")
            return True, f"{what} in {target}"
        if "409" not in result:
            break
    log(f"nova-capture failed amending {target}: {result}")
    return False, f"could not write to {target}: {result}"


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
    start, first, end = _capture_span(lines)

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
        current, rev = vault_read_path_rev(path)
        if current is None:
            return False, f"{path} not found"
        result = vault_write_path(
            path, insert_captures(current, bullets), if_rev=rev)
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
