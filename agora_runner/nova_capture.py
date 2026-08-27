"""The capture box: one field on Nova's site, one bullet in the owner's backlog.

Idea #34 item 6, and the first thing on this site that *writes* to the
vault. It replaces opening Obsidian on a phone to type one line.

**Where a capture lands, and why exactly there.** Both target files
declare their own contract in frontmatter -- *"the owner writes in the bare
bullet list at the top ... Nova numbers it, boards it, and always leaves
exactly one empty bullet there so he can start typing immediately"*. A
capture is the owner writing, so it goes in that list and nowhere else, with
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

import re
from datetime import datetime

from agora_runner.config import OSLO
from agora_runner.log import log
from agora_runner.nova_boards import (
    BOARD_PATHS,
    add_row,
    CAPTURE_PRIORITY_SEP,
    PRIORITY_LABELS,
    canonical_priority,
    append_detail_note,
    capture_entries,
    delete_row,
    _frontmatter_end,
    extract_row,
    set_row_priority,
    set_row_title,
    split_capture_done,
    split_capture_priority,
)
from agora_runner.nova_uploads import is_attachment_line
from agora_runner.vault import vault_read_path_rev, vault_write_path

# These three moved out of `projects/sokrates/projects/agora/` on
# 2026-08-12. The owner had asked whether they should follow Nova into its
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
    # The owner, issues.md 2026-08-12: *"I should be able to just leave you
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

# `amend`'s "nothing happened, the address moved" answer. Named because
# `convert_capture` and `_post_convert` both have to tell it apart from a
# failed write, and a substring match on prose typed twice is the drift
# this repo keeps filing against itself.
STALE_CAPTURE = "no longer in the list"

# Where a row the owner deletes from the app goes so a cycle can still see it
# (his capture, 2026-08-22). `resources/` because it is my bookkeeping --
# he asked to be able to remove a row from his board, not to be given a
# second page of removed rows to read.
DELETED_ROWS_PATH = "projects/sokrates/projects/agora/nova/resources/deleted-rows.md"
DELETED_ROWS_HEADER = """---
type: log
tags: [agora, nova, board, deleted]
status: built
contract: Written by agora_runner/nova_capture.py when Edvard deletes a boarded row from the Nova app. Newest last. Nothing reads this automatically -- it exists so a cycle that finds a number missing, or was mid-way through the work when he removed it, can see what the row said.
---

# Deleted board rows
"""


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


def list_captures(markdown):
    """The capture list's texts -- his words alone, without any reply."""
    return [text for _, _, text, _ in capture_entries(markdown)]


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
    any more", which is a different answer to the owner than a failed write
    and only one of them is an error.

    Passing no bullets deletes it. The empty cursor bullet is not a
    capture and is never addressable -- it is the file's contract.
    """
    entries = capture_entries(markdown)
    wanted = (original or "").strip()
    if not wanted or not isinstance(index, int) or not 0 <= index < len(entries):
        return None
    begin, end, text, replies = entries[index]
    # **His sentence alone, never the board page's folded spelling** --
    # and the reviewer is the reason this is one form rather than two.
    # Accepting the joined string looked like the fix for a dead Edit
    # button, and it is worse than the dead button: `app.js` builds the
    # *replacement* text out of the same folded string, so one tap on the
    # priority chip writes `- Rated: his line my answer` with the reply
    # still underneath it, and the next tap folds that in and doubles it
    # again. Convert does the same thing across two files. A refusal
    # leaves the board page exactly where it was before this change --
    # a button that does nothing on an answered capture, which is a
    # separate fix and belongs in the payload, not here.
    if wanted != text:
        return None
    lines = (markdown or "").split("\n")
    # A delete takes the replies with it -- an answer to a bullet that is
    # gone is orphaned text in his file, which is exactly what Cycle 415
    # spent itself cleaning up. An *edit* keeps them: he is rewording his
    # own sentence, and a cycle's answer underneath is not his to lose.
    kept = []
    if bullets:
        # From the first indented *bullet* to the end of the span -- a
        # reply and everything under it. Not "every indented line": the
        # capture's own wrapped second line is indented too, and keeping
        # that would put half his old sentence back under his new one.
        for i in range(begin, end):
            body = lines[i].strip()
            if lines[i][:1].isspace() and body.startswith("- ") and body[2:].strip():
                kept = lines[i:end]
                break
    return "\n".join(lines[:begin] + [f"- {b}" for b in bullets] + kept + lines[end:])


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
            return False, f"that capture is {STALE_CAPTURE}"
        result = vault_write_path(path, amended, if_rev=rev)
        if result == "written":
            what = "edited" if bullets else "deleted"
            log(f"nova-capture {what} a capture in {target}")
            return True, f"{what} in {target}"
        if "409" not in result:
            break
    log(f"nova-capture failed amending {target}: {result}")
    return False, f"could not write to {target}: {result}"


def reply_under_capture(markdown, index, original, text):
    """Write `text` as a cycle's reply under capture `index`. `None` if it moved.

    Same two-part address as `replace_capture` and for the same reasons:
    the index says which bullet, the text says it has not moved, and a
    disagreement is refused rather than resolved.

    The reply is an indented bullet, which is not a format invented here
    -- it is what all three parsers of these files already assume an
    indented bullet means, and what the notes page has been drawing as a
    purple bubble since Cycle 369. What was missing was any way to *write*
    one other than by hand.

    A second reply goes under the first, in file order, because `end` is
    the end of the whole span rather than of his own line.
    """
    entries = capture_entries(markdown)
    wanted = (original or "").strip()
    body = (text or "").strip()
    if not wanted or not body or not isinstance(index, int) or not 0 <= index < len(entries):
        return None
    begin, end, capture_text, replies = entries[index]
    if wanted not in (capture_text, " ".join([capture_text] + replies)):
        return None
    lines = (markdown or "").split("\n")
    return "\n".join(lines[:end] + [f"  - {body}"] + lines[end:])


def comment_on_capture(target, index, original, text):
    """Answer one unboarded capture in place. Returns (ok, message).

    **The one class of item `tools.top_board_rows` ranks above everything
    else was the one class a cycle could not answer.** His bare bullets
    outrank every boarded row, `/api/board/comment` is keyed by a row
    number, and a capture has no number -- so six handoffs in a row filed
    "no way to reply on a capture" and each one wrote its answer into a
    journal entry instead, where it is not next to the thing it answers.

    There is deliberately no `author` argument. On these three files a
    bare bullet is his and an indented one is a cycle's; that is the
    contract every parser here already reads, so an author field would be
    a second way of saying the same thing and a way for the two to
    disagree.

    Same read-modify-write and same 409 retry as `amend`, because the
    concurrent writer is the same one: a cycle boarding these files while
    the reply is being written.
    """
    path = CAPTURE_TARGETS.get(target)
    if path is None:
        return False, f"unknown target: {target!r}"
    if not (original or "").strip():
        return False, "nothing to answer"
    body = (text or "").strip()
    if not body:
        return False, "nothing to say"
    if "\n" in body or "\r" in body:
        return False, "a reply cannot contain a line break"

    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(path)
        if current is None:
            return False, f"{path} not found"
        amended = reply_under_capture(current, index, original, body)
        if amended is None:
            return False, f"that capture is {STALE_CAPTURE}"
        result = vault_write_path(path, amended, if_rev=rev)
        if result == "written":
            log(f"nova-capture replied under a capture in {target}")
            return True, f"replied in {target}"
        if "409" not in result:
            break
    log(f"nova-capture failed replying in {target}: {result}")
    return False, f"could not write to {target}: {result}"


def convert_capture(source, index, original, dest):
    """Move one unboarded capture to a different capture file. Returns (ok, message).

    The owner, capture 2026-08-24: *"The note i sent regarding the
    rebuilding the notes page was sent as a note, but its actually an
    idea, but i have no way of changing it or editing it. So we need
    crude operations for notes, but also the possibility to change
    issues/ideas/notes into one of the other."* He picks which of the
    three buttons to press at the moment he types, before he has finished
    thinking, and until now that choice was permanent -- the only way out
    was to delete the line and retype it into the other box.

    **This converts a bare bullet, not a boarded row, and that boundary is
    deliberate rather than a first slice.** A capture is one line of his
    text in a list, so moving it really is a move. A boarded row is a
    numbered row with a priority cell, a `# Details` write-up and a
    comment thread, and its number is what every journal entry, claim slug
    and board comment points at; carrying that across to another file
    means deciding what happens to the number and the thread, which is a
    different piece of work with a real design question in it. A row he
    wants moved after it is boarded is still one he can say so about.

    **Write to the destination first, then remove from the source.** The
    two files are separate documents with separate revisions, so there is
    no transaction to be had here and one of the two orders has to be
    chosen for what its half-done state costs him. Delete-then-write loses
    his sentence if the second call fails. Write-then-delete leaves the
    line in both files, which he can see and delete in one tap -- and the
    message below says so rather than reporting success. A duplicate is
    recoverable; his text is not.

    The rating rides across with the bullet for the two boards, because it
    is his and it is still true after the move. It is stripped going into
    `notes.md`, whose contract is *"never numbered, never boarded"* -- a
    priority label in a file with no board is vocabulary from a page that
    does not exist.
    """
    if source not in CAPTURE_TARGETS:
        return False, f"unknown target: {source!r}"
    if dest not in CAPTURE_TARGETS:
        return False, f"unknown target: {dest!r}"
    if source == dest:
        return False, f"already in {dest}"
    if not (original or "").strip():
        return False, "nothing to convert"

    text = original
    if dest == "notes":
        _, text = split_capture_priority(original)
        if not text.strip():
            return False, "nothing to convert"

    ok, message = capture(dest, text)
    if not ok:
        return False, message
    ok, removal = amend(source, index, original, "")
    if not ok:
        log(f"nova-capture converted {source}->{dest} but left the original: {removal}")
        # **Two failures, and telling him the wrong one costs him a
        # duplicate he cannot find.** A write that failed really does leave
        # the line in both files. A *stale address* does not: the bullet
        # was boarded, edited, or already removed by a second tap of the
        # same button, so the source may be clean and the copy in `dest`
        # may be the second one. Found by review, which walked a
        # double-tap through both calls.
        if STALE_CAPTURE in removal:
            return False, (
                f"copied to {dest}, but {source} moved under me — "
                f"check {dest} for a duplicate"
            )
        return False, (
            f"copied to {dest}, but could not remove it from {source} "
            f"({removal}) — it is in both, delete the {source} one"
        )
    log(f"nova-capture converted a capture from {source} to {dest}")
    return True, f"moved to {dest}"


# Where his first sentence ends. `. ` / `? ` / `! ` followed by a capital
# or the end of the line -- not a bare full stop, which would cut
# `sonarr.` or `08-26.` in half. A capture with no sentence break at all
# has no match and becomes its own title, whole.
_FIRST_SENTENCE_RE = re.compile(r"^(.*?[.!?])(?:\s+(?=[A-Z0-9])|\s*$)", re.DOTALL)


def capture_title(text):
    """One capture bullet -> the one-line title its board row should carry."""
    body = " ".join((text or "").split())
    match = _FIRST_SENTENCE_RE.match(body)
    return (match.group(1) if match else body).strip()


def promote_capture(target, index, original, priority=None):
    """Turn one unboarded capture into a numbered row. Returns (ok, message).

    The owner, capture 2026-08-26: *"Whats with the not boarded
    ideas/issues? I really like the comments on them so that i can see
    whats happening, but they do no seem to just stay forever in the 'not
    boarded yet' box as unrated. Thats not what the box is for. This a re
    ideas you have not seen before and you pick it up, prioritised them
    and make them as their own nice item like the rest."*

    **One write, not two, and that is the whole reason this is not shaped
    like `convert_capture`.** A capture and the board it is promoted onto
    live in the *same file* -- `issues.md` holds the bullet list at the
    top, `## Board` in the middle and `# Details` at the bottom -- so
    adding the row and removing the bullet are one read-modify-write
    against one revision, and there is no half-done state to choose a
    lesser evil between. `convert_capture` has to write two files and
    says so; this one does not and must not, because a row written by a
    first call and a bullet removed by a second would show him his own
    text twice for as long as the second call took to fail.

    The rating rides across if he set one and `priority` overrides it --
    the point of the ask is that a cycle *rates* the thing on the way
    past, and his own rating is the better default when he gave one.

    A cycle's earlier answers under the bullet ride across as dated notes
    on the write-up, so the thread he says he likes survives the move.
    `None` from `replace_capture` means the address is stale, which is
    the one failure worth telling apart: nothing has been written, and
    the page needs re-reading rather than the write retrying.
    """
    paths = BOARD_PATHS.get(target)
    path = paths.get("edvard") if paths else None
    if path is None or target not in CAPTURE_TARGETS:
        return False, f"unknown target: {target!r}"
    wanted = (original or "").strip()
    if not wanted:
        return False, "nothing to promote"

    dated = datetime.now(OSLO).strftime("%m-%d")
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(path)
        if current is None:
            return False, f"{path} not found"
        entries = capture_entries(current)
        if not isinstance(index, int) or not 0 <= index < len(entries):
            return False, f"that capture is {STALE_CAPTURE}"
        _, _, text, replies = entries[index]
        if text != wanted:
            return False, f"that capture is {STALE_CAPTURE}"
        rating, body = split_capture_priority(text)
        _, body = split_capture_done(body)
        chosen = canonical_priority(rating if priority is None else priority)
        if chosen is None:
            return False, f"unknown priority: {priority!r}"
        title = capture_title(body)
        if not title:
            return False, "nothing to promote"
        # A pipe would close the table cell it is written into and a
        # newline would end the row; `add_row` refuses both. Folding them
        # is not this function's call to make, so the refusal is passed on.
        boarded, number = add_row(
            current, title, dated, chosen, write_up=body, notes=replies)
        if boarded is None:
            return False, f"could not board {title!r}"
        updated = replace_capture(boarded, index, wanted, [])
        if updated is None:
            return False, f"that capture is {STALE_CAPTURE}"
        result = vault_write_path(path, updated, if_rev=rev)
        if result == "written":
            log(f"nova-capture promoted a {target} capture to #{number}")
            return True, f"boarded as #{number}"
        if "409" not in result:
            break
    log(f"nova-capture failed to promote a {target} capture: {result}")
    return False, f"could not write to {target}: {result}"


def clean_capture_text(text, one_item=False):
    """Text as typed -> the bullets to add.

    Each non-blank line becomes its own bullet. A multi-line paste into a
    one-line-per-item file is far more likely to be several captures than
    one wrapped thought -- and the deciding argument is structural rather
    than a guess about intent: a bullet containing a raw newline would
    break the list it lives in.

    **With one exception, and it is not a guess about intent either: a
    line the attach button wrote.** The owner, capture 2026-08-21: *"I see
    that my image upload test was split into two idea entries. The image
    for its own separate entry and the text got the other."* That is
    exactly what the rule above does to him -- `buildAttach`'s `onInsert`
    puts `![…](/api/upload/…)` in as its own paragraph, so his sentence
    files as one capture and his screenshot as another, and neither half
    means much alone. So an attachment line is folded onto the bullet
    above it with a space, which is the same joining rule
    `capture_entries` already uses to read a wrapped bullet back.

    The exception stays this narrow on purpose. It fires only on a line
    that is *nothing but* a link this site generated on his behalf
    (`nova_uploads.is_attachment_line`) -- never on a markdown image he
    typed, never on a remote URL -- so the "several captures" reasoning
    still governs every line a person actually wrote.

    **An attachment reaches forwards as well as backwards, because he can
    tap attach before he types.** The box is often empty when he picks the
    photo, and then the link is the *first* line and his sentence the
    second -- which is his own complaint mirrored, and fixing only the
    order he happened to report would leave him hitting it again the next
    day. So an attachment with nothing before it is held and joined to the
    next bullet instead. Typing order is preserved either way, so what
    lands in his file reads back in the order he built it.

    An attachment with no text on *either* side is still its own bullet:
    there is genuinely nothing to attach it to, and dropping it would lose
    the picture.

    A leading `- ` is stripped so typing the bullet character yields one
    bullet rather than `- - like this`.

    **`one_item` is the owner saying the whole paste is one thought**, and
    it exists because the structural argument above has an edge the owner
    hits with a keyboard rather than a phone. Sokrates pasted a
    thirteen-paragraph write-up of the NAS into the box on 2026-08-27 --
    one request, with a rationale, an inventory and a scope note -- and it
    filed as thirteen separate unboarded captures, because every line
    became its own bullet. His words: *"a long paste into the capture box
    has no way to signal 'this is one issue' short of avoiding blank lines
    entirely."* Cycle 545 put those thirteen back together by hand into
    issue #122; this is so the next one does not need to.

    It joins every line into a single bullet with a space, which is the
    same joining `capture_entries` does when it reads a wrapped bullet
    back, so nothing he typed is lost and the list it lands in stays
    parseable. It is off unless the caller asks for it: the default is
    still one bullet per line, and that is still the right guess for the
    phone the box was built for.
    """
    bullets = []
    # Attachment lines seen before any text line -- he attached first.
    pending = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        elif line == "-":
            line = ""
        if not line:
            continue
        if is_attachment_line(line):
            if bullets:
                bullets[-1] = bullets[-1] + " " + line
            else:
                pending.append(line)
        elif pending:
            bullets.append(" ".join(pending + [line]))
            pending = []
        else:
            bullets.append(line)
    # Nothing ever came to attach them to.
    bullets = bullets + pending
    if one_item and len(bullets) > 1:
        return [" ".join(bullets)]
    return bullets


def insert_captures(markdown, bullets):
    """Add `bullets` to the capture list, keeping exactly one empty bullet last.

    The empty bullet is the cursor the owner types into, so it stays at the
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


def capture(target, text, priority="", one_item=False):
    """Append a capture to `issues.md` or `ideas.md`. Returns (ok, message).

    `target` is a key into CAPTURE_TARGETS, never a path -- nothing a
    client sends is ever used to address a vault document.

    `priority` rides at the front of the bullet as its full label and a
    colon (`🟠 High: ...`, `CAPTURE_PRIORITY_SEP`), and only on the
    first bullet: a
    paste that splits into four lines is one thought the owner rated once,
    not four items each rated separately. It is the same rating vocabulary
    the board column uses, checked against `PRIORITY_LABELS` here as well
    as at the endpoint, because this is the function that decides what
    characters land in his file.

    It was a bare coloured glyph until Cycle 268 -- this is the one place
    colour was the *only* signal, because a bare bullet has no column to
    spell the word out in, and the owner cannot tell the four balls apart
    (comments board 2026-08-19). Cycle 268 then dropped the glyph
    entirely, which he corrected the next morning (*"if you use the
    symbol and text, thats completely fine!"*), so what gets written now
    is both: the glyph for the colour he likes, and the word without
    which the colour means nothing.
    """
    path = CAPTURE_TARGETS.get(target)
    if path is None:
        return False, f"unknown target: {target!r}"
    if priority:
        # Normalised, not exact-matched, for the reason `canonical_priority`
        # gives: a caller still on the coloured spelling must not be refused.
        submitted, priority = priority, canonical_priority(priority)
        if priority is None:
            return False, f"unknown priority: {submitted!r}"
    bullets = clean_capture_text(text or "", one_item=one_item)
    if not bullets:
        return False, "nothing to capture"
    if priority:
        bullets[0] = priority + CAPTURE_PRIORITY_SEP + bullets[0]

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


def _amend_board(target, number, mutate, what):
    """Read-modify-write one of the owner's board files. Returns (ok, message).

    The fourth and fifth write paths on this site share one loop rather
    than copying `set_priority`'s a fourth and fifth time. The 409 retry
    is the same and matters for the same reason -- a cycle boarding these
    files in step 6 is the concurrent writer, and it is the one most
    likely to be running.

    `mutate` returning `None` is not a write failure and is never
    retried: the row is not there, or the new title is not writable. A
    re-read returns the same answer and a 409 loop around it would spin.

    **The path comes from `BOARD_PATHS`, not `CAPTURE_TARGETS`, and that
    is the reviewer's point rather than mine.** The two dicts hold the
    same string for `issues` and `ideas` today and it is a coincidence:
    `CAPTURE_TARGETS` also carries `notes`, which is not a board at all,
    and `BOARD_PATHS` already splits his file from mine. Reading a board
    row's path out of the capture dict works until one of them is
    restructured for its own reasons, and then this writes somewhere else
    with nothing to say so. `["edvard"]` is also the scope boundary he
    set in #85 -- *"This is only for the ones i have reported"* -- said in
    the addressing rather than checked separately.
    """
    paths = BOARD_PATHS.get(target)
    path = paths.get("edvard") if paths else None
    if path is None:
        return False, f"unknown target: {target!r}"

    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(path)
        if current is None:
            return False, f"{path} not found"
        updated = mutate(current)
        if updated is None:
            return False, f"#{number} is not a row on {target}"
        result = vault_write_path(path, updated, if_rev=rev)
        if result == "written":
            log(f"nova-capture {what} #{number} on {target}")
            return True, f"#{number} {what} on {target}"
        if "409" not in result:
            break
    log(f"nova-capture failed to {what} #{number} on {target}: {result}")
    return False, f"could not write to {target}: {result}"


def edit_row(target, number, title):
    """Retitle one boarded row. Returns (ok, message).

    The owner, issue #84: *"I need to be able to edit and especially delete
    boarded ideas and issues from the agora app. If i hold the card for
    more than 1 second i get into edit mode and also have the option of
    deleting, save or cancel the edit."*

    **What "edit" means here is the title, and that is a judgement worth
    stating.** A boarded card carries a title he wrote, a status and a
    date I maintain, and a write-up that is my prose about his item. The
    status already has a picker, the date is bookkeeping, and the
    write-up is mine to be wrong in -- so the one thing on that card he
    might want to correct and currently cannot is the sentence he typed.
    If he wants the write-up editable too, that is one more field and he
    can say so in a sentence.
    """
    return _amend_board(
        target, number, lambda md: set_row_title(md, number, title), "edited")


def remove_row(target, number):
    """Delete one boarded row and its write-up. Returns (ok, message).

    *"and especially delete"*. Irreversible from the app's side, which is
    why the route is separate from the edit and why `app.js` asks first --
    the same shape `/api/capture/delete` already has. It is not
    irreversible in the vault: CouchDB keeps the revision, and Obsidian
    LiveSync tombstones rather than removes.

    **And a cycle can now see that it happened**, which is the second half
    of his 2026-08-22 capture: *"Maybe the delete function should tell
    your next cycle that i have deleted it just in case some work was
    being done or just to keep it as a deleted issue for future
    reference."* CouchDB keeping the revision was already true and is not
    an answer -- nothing in a cycle's opening read fetches an old revision
    of a 190KB file to diff it, so a row he removed mid-cycle was
    indistinguishable from one that never existed.

    So the deleted text is copied into `resources/deleted-rows.md` before
    the row goes. It goes in `resources/` and not beside his boards
    because it is bookkeeping for me, not a page he asked to read.

    **A failed archive does not fail the delete.** He pressed a button
    that says Delete and got a confirmation; refusing afterwards would
    leave him unable to remove a row because a file he has never heard of
    would not write. The archive is logged instead, and the audit trail in
    `nova_site` records the deletion either way.
    """
    captured = {}

    def mutate(markdown):
        updated = delete_row(markdown, number)
        if updated is not None:
            captured["text"] = extract_row(markdown, number)
        return updated

    ok, message = _amend_board(target, number, mutate, "deleted")
    if ok:
        _archive_deleted_row(target, number, captured.get("text"))
    return ok, message


def _archive_deleted_row(target, number, text):
    """Append one deleted row to `resources/deleted-rows.md`. Never raises."""
    if not text:
        log(f"nova-capture: nothing captured for deleted #{number} on {target}")
        return
    stamp = datetime.now(OSLO).strftime("%Y-%m-%d %H:%M")
    # A write-up is markdown and routinely carries its own fenced blocks
    # and `###` subheadings. Fencing it keeps those from becoming
    # structure in *this* file; the fence has to out-run the longest run
    # of backticks inside it or the block closes early and the rest of the
    # row leaks out as headings.
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    entry = (
        f"\n## {target} #{number} — deleted {stamp} Oslo\n\n"
        f"{fence}\n{text}\n{fence}\n"
    )
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(DELETED_ROWS_PATH)
        if current is None:
            current, rev = DELETED_ROWS_HEADER, None
        result = vault_write_path(
            DELETED_ROWS_PATH, current.rstrip("\n") + "\n" + entry, if_rev=rev)
        if result == "written":
            return
        if "409" not in result:
            break
    log(f"nova-capture could not archive deleted #{number} on {target}: {result}")


def comment_on_row(target, number, comment, dated, author="Edvard"):
    """Add one comment to a boarded row's write-up. (ok, message)

    Idea #64, rated 🔴 Immediately and open since 2026-08-12: *"Lets me
    have the same comment conversation on ideas, notes and issues like
    the Journal. Add a comment button and let me leave comments that
    discuss each idea."*

    **The read half of this was already built and nobody noticed.** An
    expanded board row lazily fetches its write-up and renders it, so a
    line appended to that write-up appears on his phone with no change to
    the page at all -- which is why this is one write path and not a
    feature. The design call (Cycle 190's, in the #64 write-up) was
    inline over a second comments file, and inline is what makes the read
    half free: *"a comment and my answer to it sit in the same place as
    the idea, so a cycle reading the idea cannot miss the conversation
    about it."*

    So there is deliberately no `## New` queue here, unlike
    `nova_comments`. The queue exists there because a journal comment
    has nowhere else to live; a board comment lands in a file every cycle
    already reads, under the row it is about. What a cycle owes it is a
    reply on the next line -- same call, `author="Nova"`.

    `_amend_board` gives it the 409 retry the other four write paths have,
    and it matters more here than anywhere: the concurrent writer is a
    cycle appending to these same write-ups in step 6.
    """
    return _amend_board(
        target,
        number,
        lambda md: append_detail_note(md, number, comment, dated, author=author),
        "commented on",
    )


def set_priority(target, number, priority):
    """Change one boarded row's rating. Returns (ok, message).

    The third write path on this site, and the first that edits something
    *I* wrote rather than something the owner wrote. Same read-modify-write
    and same 409 retry as `capture` and `amend`, for the same reason: a
    cycle boarding these very files is the concurrent writer, and it is
    the one most likely to be running, since boarding is what step 6 of
    every cycle does.

    `set_row_priority` returning `None` is not a write failure and is not
    retried -- the row is gone, done, or the rating is not one of the four.
    Re-reading would return the same answer and a 409 loop around it would
    just spin, which is the distinction `amend` draws too.
    """
    path = CAPTURE_TARGETS.get(target)
    if path is None:
        return False, f"unknown target: {target!r}"

    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(path)
        if current is None:
            return False, f"{path} not found"
        updated = set_row_priority(current, number, priority)
        if updated is None:
            return False, f"#{number} is not an open row on {target}"
        result = vault_write_path(path, updated, if_rev=rev)
        if result == "written":
            log(f"nova-capture rated #{number} on {target} as {priority or '(unrated)'}")
            return True, f"#{number} is now {priority or 'unrated'}"
        if "409" not in result:
            break
    log(f"nova-capture failed rating #{number} on {target}: {result}")
    return False, f"could not write to {target}: {result}"


