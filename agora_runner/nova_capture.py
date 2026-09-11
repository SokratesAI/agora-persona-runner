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

from agora_runner import (
    board_document, board_records, board_store, board_view, board_write,
    entity_id, rank_key)
from agora_runner.board_store import CaptureConflict, RegistryConflict
from agora_runner.config import OSLO
from agora_runner.log import log
from agora_runner.nova_boards import (
    _CLOSED_STATUS_KEYS,
    CAPTURE_PRIORITY_SEP,
    MILESTONE_PINS_PATH,
    PROJECT_META_PATH,
    parse_project_meta,
    set_milestone_pin as _set_milestone_pin_md,
    set_project_priority as _set_project_priority_md,
    set_project_order as _set_project_order_md,
    set_project_satisfaction as _set_project_satisfaction_md,
    resolve_project_lifecycle as _resolve_project_lifecycle_md,
    canonical_priority,
    append_detail_note,
    capture_entries,
    _frontmatter_end,
    OUTDATED_STATUS,
    STATUS_LABELS,
    sparse_row_order_seats as _row_order_seats,
    priority_key,
    status_key,
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
# `projects/sokrates/projects/nova/` is a different folder from
# `projects/sokrates/projects/agora/nova/` -- one is his, one is Nova's,
# they differ by a path segment -- but since 2026-09-02 both route to
# Nova's database, at his ask. Until then this comment said adding his
# prefix to `NOVA_DB_FOLDERS` "would take the only three files he writes
# by hand off his phone"; it does not, because he writes them through the
# Nova app and the app reads through the same rule. What it does take
# away is editing them in Obsidian. `test_vault_database_routing` pins
# these three to Nova's database now, and the pin still matters in the
# same way: a capture file that drifts out of a routed folder would be
# written to one store and read from another.
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
    # His capture 2026-09-08, with a screenshot of the box: *"another
    # [button] that is like issues and ideas, but it says 'project'."*
    #
    # A flat fourth file, with the same bare-bullet contract as the other
    # three, and deliberately **not** a real project note per capture.
    # The project board reads frontmatter -- lifecycle, TRL, size, order --
    # and a one-line phone capture has none of it, so writing a note per
    # capture would create a project row that is wrong in four fields the
    # moment it exists. A cycle promotes a bullet here into a real project
    # the same way it boards an issue.
    #
    # Same obligation as `notes` above, and it is the half that makes this
    # a button rather than a dead end: `prompt.md` step 1a has to read this
    # file, or a capture lands where nothing opens it.
    #
    # **Not `projects.md`, which the spec proposed and which is already
    # taken.** `nova_boards.PROJECT_META_PATH` is that path: the project
    # *rating* board, a markdown table of one row per rated project, read
    # by `parse_project_meta` on every board render. Filing bare bullets
    # into it would put untabled lines above a table whose parser is the
    # ordering of the whole board. The file this writes is new and holds
    # nothing else.
    "projects": "projects/sokrates/projects/nova/proposed-projects.md",
}


# The project a capture belongs to rides on the end of the bullet as a
# tag: `- fix the drag on the tool sheet #nova-app`.
#
# **The bare-bullet contract is what picks the format, not taste.** Every
# parser in this module -- `capture_entries`, `replace_capture`,
# `split_capture_priority` -- assumes a capture is one line that looks
# exactly like a line he typed in Obsidian. A prefix or a second line
# would be richer and would break all three. A trailing tag survives them
# untouched, greps, reads as a tag in Obsidian, and he can type one by
# hand.
#
# The slug comes from the project name he picked in the app, lowercased
# with runs of anything that is not a letter, a digit or a hyphen
# collapsed to one hyphen. That is one source of truth: the picker offers
# the names the board already holds, so a tag can only ever name a
# project that exists at the moment it is written.
PROJECT_TAG_PREFIX = "#"

_PROJECT_SLUG_RE = re.compile(r"[^a-z0-9]+")


def project_slug(name):
    """A project name -> the tag slug a capture carries it as. `""` if none."""
    slug = _PROJECT_SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug

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


def amend(target, index, original, text, store=None):
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

    **His two boards go to the #203 record store** (`_amend_records`); only
    `notes`, which has no records, still reads and writes the file here.
    """
    path = CAPTURE_TARGETS.get(target)
    if path is None:
        return False, f"unknown target: {target!r}"
    if not (original or "").strip():
        return False, "nothing to amend"
    bullets = clean_capture_text(text or "")
    board = RECORD_BOARDS.get(target)
    if board is not None:
        return _amend_records(target, board, index, original.strip(), bullets,
                              store or board_store)

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


def _amend_records(target, board, index, wanted, bullets, store):
    """`amend` on one of his two boards: the capture's record, never his file.

    The same two-part address as `replace_capture`: `board_records.capture_at`
    finds the bullet at the position his page showed, and its own words must
    still read `wanted`, or the answer is `STALE_CAPTURE` and nothing is
    written. An edit is `board_write.change_capture_text`, which keeps the
    replies under the bullet and checks the rest of the board came back
    untouched; a delete is `board_store.delete_capture`, which takes the
    replies with it, as the markdown delete did.

    **The conflict retry survives the move, and it is the markdown loop's 409
    retry on a different store.** Both writes are conditional on the revision
    `capture_at` read, so a cycle replying under this bullet in between is a
    `CaptureConflict`: re-read, re-check the words, write again -- and a
    re-read that no longer finds them is stale rather than a resurrection.
    A delete that finds the document already gone (a second tap) is stale too.

    **One bullet only.** The file path turns a multi-line edit into several
    bullets; the records have no capture-insert door until the capture box
    itself moves, so that edit is refused here rather than half-written.
    """
    if len(bullets) > 1:
        return False, (
            "an edit here is one bullet -- use the capture box to add the "
            "others")
    new_text = bullets[0] if bullets else ""
    problem = None
    for _ in range(WRITE_ATTEMPTS):
        try:
            doc = board_records.capture_at(board, index, store=store)
        except Exception as error:  # noqa: BLE001 -- any failure is "not written"
            log(f"nova-capture could not read the {target} records: {error}")
            return False, f"could not read {target}: {error}"
        if doc is None or board_document.capture_text_of(doc) != wanted:
            return False, f"that capture is {STALE_CAPTURE}"
        try:
            if not new_text:
                if not store.delete_capture(doc):
                    return False, f"that capture is {STALE_CAPTURE}"
                log(f"nova-capture deleted a capture in {target}")
                return True, f"deleted in {target}"
            if new_text != wanted:
                board_write.change_capture_text(board, doc, new_text, store=store)
            log(f"nova-capture edited a capture in {target}")
            return True, f"edited in {target}"
        except CaptureConflict as error:
            problem = error
            continue
        except (board_write.WriteRefused, board_write.BoardDamaged,
                board_records.RecordError) as error:
            problem = error
            break
    log(f"nova-capture failed amending {target}: {problem}")
    return False, f"could not write to {target}: {problem}"


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


def comment_on_capture(target, index, original, text, store=None):
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

    **His two boards go to the #203 record store** (`_reply_records`); only
    `notes`, which has no records, still reads and writes the file here.
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
    board = RECORD_BOARDS.get(target)
    if board is not None:
        return _reply_records(target, board, index, original.strip(), body,
                              store or board_store)

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


def _reply_records(target, board, index, wanted, body, store):
    """`comment_on_capture` on one of his two boards: the capture's record.

    The same address as `reply_under_capture` -- the position his page showed,
    and his words (or the older folded spelling, his words with the replies
    welded on) must still be there -- and the reply goes on the end of the
    record's `replies`, under any earlier one, as the file put it.

    The write is `write_capture` on the document as read, so it is
    conditional on that revision: a second writer on this bullet in between is
    a `CaptureConflict`, answered by re-reading and re-checking the words, the
    markdown loop's 409 retry on a different store. Nothing else on the board
    is touched, because nothing else is sent.
    """
    problem = None
    for _ in range(WRITE_ATTEMPTS):
        try:
            doc = board_records.capture_at(board, index, store=store)
        except Exception as error:  # noqa: BLE001 -- any failure is "not written"
            log(f"nova-capture could not read the {target} records: {error}")
            return False, f"could not read {target}: {error}"
        if doc is None:
            return False, f"that capture is {STALE_CAPTURE}"
        text = board_document.capture_text_of(doc)
        replies = board_document.capture_replies_of(doc)
        if wanted not in (text, " ".join([text] + replies)):
            return False, f"that capture is {STALE_CAPTURE}"
        try:
            store.write_capture(dict(doc, replies=replies + [body]))
        except CaptureConflict as error:
            problem = error
            continue
        except Exception as error:  # noqa: BLE001 -- any failure is "not written"
            problem = error
            break
        log(f"nova-capture replied under a capture in {target}")
        return True, f"replied in {target}"
    log(f"nova-capture failed replying in {target}: {problem}")
    return False, f"could not write to {target}: {problem}"


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


def promote_capture(target, index, original, priority=None, store=None):
    """Turn one unboarded capture into a numbered row. Returns (ok, message).

    The owner, capture 2026-08-26: *"Whats with the not boarded
    ideas/issues? I really like the comments on them so that i can see
    whats happening, but they do no seem to just stay forever in the 'not
    boarded yet' box as unrated. Thats not what the box is for. This a re
    ideas you have not seen before and you pick it up, prioritised them
    and make them as their own nice item like the rest."*

    **Records only, since #203: both boards a capture can be promoted on are
    in the record store, and `notes` has no board to promote onto.** The row
    is `board_write.add_row` -- it mints the number from the store, puts the
    row at the top as the file did, and checks the board afterwards -- and
    the bullet then goes with `board_store.delete_capture` on the document
    as read.

    **Two writes now, where the file made it one, so the order is chosen
    for what the half-done state costs him** -- `convert_capture`'s call.
    Row first: if the delete then fails, his text is on the board AND in the
    box, which he can see and clear in one tap, and the message says so.
    Delete first would lose his sentence to a failed row write.

    The rating rides across if he set one and `priority` overrides it --
    the point of the ask is that a cycle *rates* the thing on the way
    past, and his own rating is the better default when he gave one.

    A cycle's earlier answers under the bullet ride across as dated notes
    on the write-up, so the thread he says he likes survives the move.
    A stale address -- a second tap, or a bullet boarded by someone else --
    writes nothing, and the page needs re-reading rather than a retry.
    """
    board = RECORD_BOARDS.get(target)
    if board is None:
        return False, f"unknown target: {target!r}"
    store = store or board_store
    wanted = (original or "").strip()
    if not wanted:
        return False, "nothing to promote"

    dated = datetime.now(OSLO).strftime("%m-%d")
    try:
        doc = board_records.capture_at(board, index, store=store)
    except Exception as error:  # noqa: BLE001 -- any failure is "not written"
        log(f"nova-capture could not read the {target} records: {error}")
        return False, f"could not read {target}: {error}"
    if doc is None or board_document.capture_text_of(doc) != wanted:
        return False, f"that capture is {STALE_CAPTURE}"
    rating, body = split_capture_priority(wanted)
    _, body = split_capture_done(body)
    chosen = canonical_priority(rating if priority is None else priority)
    if chosen is None:
        return False, f"unknown priority: {priority!r}"
    title = capture_title(body)
    if not title:
        return False, "nothing to promote"
    try:
        # A pipe would close the table cell the generated view draws and a
        # newline would end the row; `add_row` refuses both. Folding them is
        # not this function's call to make, so the refusal is passed on.
        row = board_write.add_row(
            board, title, dated, chosen, write_up=body,
            notes=board_document.capture_replies_of(doc), store=store)
    except board_write.WriteRefused as error:
        return False, f"could not board {title!r}: {error}"
    except Exception as error:  # noqa: BLE001 -- any failure is "not written"
        log(f"nova-capture failed to promote a {target} capture: {error}")
        return False, f"could not write to {target}: {error}"
    number = row["number"]
    try:
        removed = store.delete_capture(doc)
    except Exception as error:  # noqa: BLE001 -- a conflict included
        log(f"nova-capture boarded a {target} capture as #{number} but left "
            f"the bullet: {error}")
        return False, (
            f"boarded as #{number}, but could not take the bullet out of the "
            f"box ({error}) — check the box for it and delete it there")
    if not removed:
        # `False` is `delete_capture`'s "already gone", not a failure: the
        # bullet left the box between the read and the delete. The one writer
        # that does that is a second Board on the same bullet, which will have
        # minted its own row -- so this is done, and says where a duplicate
        # would be rather than sending him to a box that is already clean.
        # Reviewer finding, Cycle 1390.
        log(f"nova-capture boarded a {target} capture as #{number}; the "
            "bullet had already left the box")
        return True, (
            f"boarded as #{number} — the bullet had already left the box, so "
            "check the board for a second row from a double tap")
    log(f"nova-capture promoted a {target} capture to #{number}")
    return True, f"boarded as #{number}"


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


def capture(target, text, priority="", one_item=False, project="", store=None):
    """Add a capture to one of his three boxes. Returns (ok, message).

    **His two boards go to the #203 record store** (`_capture_records`);
    only `notes`, which has no records, still reads and writes the file here.

    `target` is a key into CAPTURE_TARGETS, never a path -- nothing a
    client sends is ever used to address a vault document.

    `project` rides at the *end* of the first bullet as a `#slug` tag --
    see `project_slug`; the front is taken and the end is the only place
    a bare bullet has left.

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
    # The project tag, on the first bullet only and for the same reason
    # the rating is: a paste that splits into four lines is one thought he
    # filed against one project, not four items each assigned separately.
    # An unnameable project (a name that slugs to nothing) writes no tag
    # rather than a bare `#`, which would be a tag pointing at nothing.
    slug = project_slug(project)
    if slug:
        bullets[0] = bullets[0] + " " + PROJECT_TAG_PREFIX + slug
    board = RECORD_BOARDS.get(target)
    if board is not None:
        return _capture_records(target, board, bullets, store or board_store)

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


def _capture_records(target, board, bullets, store):
    """`capture` on one of his two boards: one new capture record per bullet.

    Same place as `insert_captures` puts them: below every capture already
    there, in the order they were typed: `rank_key.between(last, None)`. A
    capture rank is a `rank_key` and nothing else -- `tools.board_migrate`
    seeds them with `rank_key.sequence` since Cycle 1391 and
    `to_capture_document` refuses any other shape -- so a board still holding
    an old whole-number rank fails the add loudly rather than growing a second
    shape beside it.

    **The ids are minted and the registry written before any capture is.**
    `entity_id.mint_capture` is a high-water mark, so an id written into the
    registry and then never used is a gap and costs nothing, while a capture
    stored under an id the registry never recorded is one the next mint hands
    out again. A `RegistryConflict` is a second writer minting at the same
    time: re-read and mint again, never resend -- `write_registry`'s rule.

    Nothing here rewrites a stored capture, so the owner's other bullets and
    the replies under them cannot be touched; each write creates one document.
    Two phones adding at once can take the same rank, and both bullets still
    land -- their order between each other is then CouchDB's id order.
    """
    problem = None
    for _ in range(WRITE_ATTEMPTS):
        try:
            registry = store.read_registry()
            held = board_records.capture_documents(board, store=store)
            ranked = [doc["rank"] for doc in held if doc.get("rank") is not None]
            last = ranked[-1] if ranked else None
            ranks = []
            for _ in bullets:
                last = rank_key.between(last, None)
                ranks.append(last)
            # A registry whose high-water lags the stored ids would hand out
            # one of his existing captures' ids -- which the real store refuses
            # forever and a blind one overwrites -- so mint past any id already
            # stored. `mint_capture` bumps the mark each call, which repairs it.
            taken = {doc.get("captureId") for doc in held}
            ids = []
            for _ in bullets:
                capture_id = entity_id.mint_capture(registry, board)
                while capture_id in taken:
                    capture_id = entity_id.mint_capture(registry, board)
                ids.append(capture_id)
            store.write_registry(registry)
        except RegistryConflict as error:
            problem = error
            continue
        except Exception as error:  # noqa: BLE001 -- any failure is "not written"
            log(f"nova-capture could not mint on the {target} records: {error}")
            return False, f"could not write to {target}: {error}"
        break
    else:
        log(f"nova-capture failed writing to {target}: {problem}")
        return False, f"could not write to {target}: {problem}"

    written = 0
    try:
        for text, capture_id, rank in zip(bullets, ids, ranks):
            store.write_capture(board_document.to_capture_document(
                text, board, capture_id, rank=rank))
            written += 1
    except Exception as error:  # noqa: BLE001 -- any failure is "not written"
        log(f"nova-capture failed writing to {target} after {written} of "
            f"{len(bullets)}: {error}")
        return False, (f"could not write to {target}: {error} "
                       f"({written} of {len(bullets)} landed)")
    log(f"nova-capture wrote {len(bullets)} bullet(s) to {target}")
    return True, f"captured to {target}"


def edit_row(target, number, title, store=None):
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

    **Written to the #203 record store, not to his markdown** -- the third of
    the app's board writers off the file, after `set_priority` and
    `comment_on_row`. The title is one key on the row's record. The markdown
    version moved three copies of it by hand (the cell, the wiki-link, the
    write-up heading); the generated view draws all three from that one key,
    so there is nothing left to keep in step.

    A missing row comes back as `"#N is not a row on <target>"`, the phrase
    `_post_board_edit` answers 409 on. Checked here, off a read, rather than
    read off `change_row`'s `WriteRefused`: that exception also means "the
    row moved between your read and your write", and a second tap lands on
    that one, so it must stay a 502. The title goes through
    `board_write.refuse_cell`, which also refuses a bare `\\r` --
    `set_row_title` never did, and CommonMark breaks the row on it.

    **No retry**, for `set_priority`'s reason. `store` is for tests, looked
    up at call time.
    """
    board = RECORD_BOARDS.get(target)
    if board is None:
        return False, f"unknown target: {target!r}"
    title = (title or "").strip()
    refused = board_write.refuse_cell(title, "the title")
    if refused:
        return False, f"could not retitle #{number} on {target}: {refused}"
    store = store or board_store
    try:
        before = board_records.contents(board, store=store)
    except Exception as problem:  # noqa: BLE001 -- any failure is "not written"
        log(f"nova-capture could not read the {target} records: {problem}")
        return False, f"could not read {target}: {problem}"
    if not any(item.get("number") == number for item in before["items"]):
        return False, f"#{number} is not a row on {target}"
    try:
        board_write.change_row(board, number, {"title": title}, store=store)
    except (board_write.WriteRefused, board_write.BoardDamaged,
            board_records.RecordError) as problem:
        log(f"nova-capture failed retitling #{number} on {target}: {problem}")
        return False, f"could not write to {target}: {problem}"
    log(f"nova-capture edited #{number} on {target}")
    return True, f"#{number} edited on {target}"


def archive_row(target, number, dated=None, store=None):
    """Close one boarded row as `⚫ Outdated`. Returns (ok, message).

    The owner, `issues.md` capture 2026-09-03: *"We should be able to
    archive issues and ideas. Some of the written issues and ideas do not
    have a definition of done, like issue 162. So you should be able to
    just archive it when you feel its just cluttering and does not create
    value. I should also be able to archive them. Add a archive button
    next to the delete when in edit mode."*

    **This adds a button, not a status.** `⚫ Outdated` has existed since
    Cycle 202 and already means exactly what he is asking for -- the row
    is finished with and was never built -- and `_CLOSED_STATUS_KEYS`
    already drops it out of the ranking `top_board_rows` prints, which is
    the clutter he is describing. What did not exist was any way to reach
    it from the app: a cycle could set it from a shell, he could not set
    it at all. Inventing a sixth status here would have given the same
    state two spellings, which is the drift the comment over
    `OUTDATED_STATUS` is written to prevent.

    **Archive is not delete and the two routes stay separate.** A deleted
    row's text is copied into `resources/deleted-rows.md` because the row
    is gone; an archived row keeps its number, its title and its write-up
    in his file, and comes back with one status change. That is why this
    one does not ask for a confirmation on the page and Delete does.

    **Written to the #203 record store, not to his markdown** -- the fourth
    of the app's board writers off the file, after `set_priority`,
    `comment_on_row` and `edit_row`. It is one `change_row` over five keys:
    the status and its key, the rating and its key cleared (a chip on a row
    nobody will build is the same noise as a chip on a shipped one, which is
    what `set_row_status` did in the markdown), and `updated` stamped.

    A closed row is refused off the same read that finds it. That covers a
    row in the finished table, which `from_document` gives the `done` status
    key whatever its cell says, and a `## Board` row that already reads
    `✅ Done` or `⚫ Outdated`. From this button a shipped row turning
    Outdated would be a lie about history -- `⚫ Outdated` means "never
    built". A missing row answers with `edit_row`'s 409 phrase, decided off
    the read for `edit_row`'s reason: `WriteRefused` also means "moved under
    you" and that one must stay a 502.

    **No retry**, for `set_priority`'s reason. `store` is for tests, looked
    up at call time.
    """
    board = RECORD_BOARDS.get(target)
    if board is None:
        return False, f"unknown target: {target!r}"
    stamp = dated or datetime.now(OSLO).strftime("%m-%d")
    refused = board_write.refuse_cell(stamp, "the date")
    if refused:
        return False, f"could not archive #{number} on {target}: {refused}"
    store = store or board_store
    try:
        before = board_records.contents(board, store=store)
    except Exception as problem:  # noqa: BLE001 -- any failure is "not written"
        log(f"nova-capture could not read the {target} records: {problem}")
        return False, f"could not read {target}: {problem}"
    row = next(
        (item for item in before["items"] if item.get("number") == number), None)
    if row is None:
        return False, f"#{number} is not a row on {target}"
    if row.get("statusKey") in _CLOSED_STATUS_KEYS:
        label = (STATUS_LABELS["done"] if row["statusKey"] == "done"
                 else OUTDATED_STATUS)
        return False, f"#{number} is already {label} on {target}"
    changes = {
        "status": OUTDATED_STATUS,
        "statusKey": status_key(OUTDATED_STATUS),
        "priority": "",
        "priorityKey": priority_key(""),
        "updated": stamp,
    }
    try:
        board_write.change_row(board, number, changes, store=store)
    except (board_write.WriteRefused, board_write.BoardDamaged,
            board_records.RecordError) as problem:
        log(f"nova-capture failed archiving #{number} on {target}: {problem}")
        return False, f"could not write to {target}: {problem}"
    log(f"nova-capture archived #{number} on {target}")
    return True, f"#{number} archived on {target}"


def set_project_priority(project, priority, dated=None):
    """Rate a project. Returns (ok, message).

    The sixth write path on this site and the first that does not write to
    one of his two boards -- a project-level rating has no row to live on,
    so it goes to `PROJECT_META_PATH`. Same read-modify-write and same 409
    retry as the other five, and for the same reason: a cycle boarding his
    files is the concurrent writer.

    A file that does not exist yet is not an error here. `vault_read_path_rev`
    answers `None` for a missing document, and `set_project_priority` in
    `nova_boards` writes the template whole in that case -- so the first
    rating creates the file rather than failing on it. The `if_rev` is
    passed through unchanged, so two cycles rating two projects in the same
    second still cannot both create it.

    `None` back from the markdown layer is a refusal, not a write failure,
    and is not retried: the name or the rating is out of bounds and
    re-reading gives the same answer, the same distinction `set_priority`
    draws.
    """
    if dated is None:
        # Oslo, not UTC, and stamped here rather than by the caller so the
        # one write path owns the one clock. Rule 7: anything he reads.
        dated = datetime.now(OSLO).strftime("%m-%d")
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(PROJECT_META_PATH)
        updated = _set_project_priority_md(current or "", project, priority, dated=dated)
        if updated is None:
            return False, f"cannot rate {project!r} as {priority!r}"
        result = vault_write_path(PROJECT_META_PATH, updated, if_rev=rev)
        if result == "written":
            log(f"nova-capture rated project {project!r} as {priority or '(unrated)'}")
            return True, f"{project} is now {priority or 'unrated'}"
        if "409" not in result:
            break
    log(f"nova-capture failed rating project {project!r}: {result}")
    return False, f"could not write project ratings: {result}"


def set_project_order(project, position):
    """Place a project at `position` in his hand-ranked list. Returns (ok, message).

    Milestone M3 of idea #260. Same read-modify-write and same 409 retry as
    `set_project_priority` one function up, against the same document, and
    for the same reason -- a cycle boarding his files is the concurrent
    writer.

    **A missing file is a refusal here, unlike a rating.** A rating creates
    the table because the first rating has to be able to land somewhere; a
    position is a statement about a list, and a list nobody has written has
    no positions in it. `set_project_order` in `nova_boards` answers `None`
    for that, and for a project with no row, and for a position outside the
    list -- none of the three is retried, because re-reading gives the same
    answer.
    """
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(PROJECT_META_PATH)
        updated = _set_project_order_md(current or "", project, position)
        if updated is None:
            return False, f"cannot place {project!r} at {position!r}"
        result = vault_write_path(PROJECT_META_PATH, updated, if_rev=rev)
        if result == "written":
            log(f"nova-capture placed project {project!r} at {position}")
            return True, f"{project} is now #{position}"
        if "409" not in result:
            break
    log(f"nova-capture failed placing project {project!r}: {result}")
    return False, f"could not write project order: {result}"


def pin_milestone(project, milestone, position):
    """Pin one milestone to a position inside its project. Returns (ok, message).

    Milestone M4 of idea #260, and the write end of the override half:
    *"I want the ability to reorder tasks and milestones but the default
    is that you do it"*. The formula in `nova_next.milestone_ranks` is
    the default; this is him saying otherwise about one milestone.

    Same read-modify-write and same 409 retry as `set_project_order`
    above, and for the same reason -- a cycle running `tools.milestone_pin`
    against the same document is the concurrent writer -- but against
    `MILESTONE_PINS_PATH` rather than `projects.md`, because a pin is a
    decision about a milestone and not a column on a project.

    **A missing file is created here, unlike a position and like a
    rating.** The distinction is not arbitrary: `projects.md` is a list,
    and a list nobody has written has no positions in it, so placing a
    project into nothing is a refusal. `milestones.md` is a set of
    overrides, and the first override has to be able to land somewhere.
    `set_milestone_pin` writes the header itself when handed `""`.

    **`position` 0 removes the row**, which is the only way back to the
    computed order, so the message says "unpinned" rather than "#0" --
    a position of zero is not a place in a list.
    """
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(MILESTONE_PINS_PATH)
        updated = _set_milestone_pin_md(
            current or "", project, milestone, position,
            updated=datetime.now(OSLO).strftime("%m-%d"))
        if updated is None:
            return False, f"cannot pin {milestone!r} at {position!r}"
        result = vault_write_path(MILESTONE_PINS_PATH, updated, if_rev=rev)
        if result == "written":
            log(f"nova-capture pinned milestone {milestone!r} "
                f"of {project!r} at {position}")
            if position:
                return True, f"{milestone} is now #{position} in {project}"
            return True, f"{milestone} is unpinned"
        if "409" not in result:
            break
    log(f"nova-capture failed pinning milestone {milestone!r}: {result}")
    return False, f"could not write milestone pins: {result}"


def set_project_satisfaction(project, score):
    """Record how satisfied he is with a project, 1-5. Returns (ok, message).

    Milestone M5 of idea #260, and the only write path this field has:
    *"satisfaction 1-5: mine alone"*. Same read-modify-write and same 409
    retry as `set_project_order` one function up, against the same
    document, and for the same reason -- a cycle boarding his files is the
    concurrent writer.

    **A missing file is a refusal here, the same as a position and unlike a
    rating.** A rating creates the table because the first rating has to
    land somewhere; a satisfaction score is a judgement about a project,
    and a project that has never been rated has no row to judge.

    `""` clears the score back to unrated and is not the same as 1 -- the
    spec hangs an automatic diagnosis off "2 or below", and that must never
    fire because nobody has pressed anything.
    """
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(PROJECT_META_PATH)
        updated = _set_project_satisfaction_md(current or "", project, score)
        if updated is None:
            return False, f"cannot score {project!r} as {score!r}"
        result = vault_write_path(PROJECT_META_PATH, updated, if_rev=rev)
        if result == "written":
            log(f"nova-capture scored project {project!r} at {score or '(unrated)'}")
            shown = f"{score} of 5" if score else "unrated"
            return True, f"{project} is now {shown}"
        if "409" not in result:
            break
    log(f"nova-capture failed scoring project {project!r}: {result}")
    return False, f"could not write project satisfaction: {result}"


def resolve_project_lifecycle(project, decision):
    """Approve or decline a proposed lifecycle stage. Returns (ok, message).

    Milestone M5 of idea #260: *"lifecycle: I approve / Nova proposes"*.
    This is his half and the only write path it has, the same shape as
    `set_project_satisfaction` one function up. The proposing half is
    `tools.project_lifecycle`, a CLI, because that half is mine.

    A missing file is a refusal for `set_project_order`'s reason, and so is
    a decision on a project with nothing proposed -- `resolve_project_lifecycle`
    in `nova_boards` answers `None` for both, and neither is retried,
    because re-reading gives the same answer.
    """
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(PROJECT_META_PATH)
        updated = _resolve_project_lifecycle_md(current or "", project, decision)
        if updated is None:
            return False, f"cannot {decision!r} a lifecycle for {project!r}"
        result = vault_write_path(PROJECT_META_PATH, updated, if_rev=rev)
        if result == "written":
            log(f"nova-capture {decision}d lifecycle for project {project!r}")
            settled = "approved" if decision == "approve" else "declined"
            return True, f"{project}: proposal {settled}"
        if "409" not in result:
            break
    log(f"nova-capture failed resolving lifecycle for {project!r}: {result}")
    return False, f"could not write project lifecycle: {result}"


def project_priorities():
    """Every project rating, read fresh. `{lowercased name: {...}}`.

    Uncached on purpose, the same call `/api/comments` makes: this file is
    small, it is read once per project page, and a stale rating is a
    reordered page that disagrees with the picker he is looking at.

    **A read that fails answers "nothing is rated", not an error.** This is
    a second vault fetch on the critical path of a page that worked without
    it for a week, and the ranking is the least important thing on that
    page -- so a CouchDB blip must cost him the ordering, never the rows.
    The failure is logged rather than swallowed, because a page that has
    quietly stopped ranking looks exactly like a board nobody has rated.
    """
    try:
        current, _rev = vault_read_path_rev(PROJECT_META_PATH)
    except Exception as e:
        log(f"nova-capture could not read project ratings: {e}")
        return {}
    return parse_project_meta(current or "")


def set_project(target, number, project, store=None):
    """Move one boarded row to a project. Returns (ok, message).

    His capture, 2026-09-01: *"I/you should easily be able to assign
    issues and ideas to projects, and change project if assigned wrongly
    or for some other reason needs to change project. I/you should easily
    be able to create new projects."* `set_row_project` has existed since
    the project column was added and only ever had CLI callers, so until
    now the only way he could correct a project cell was Obsidian --
    which is exactly what `edit_row` was written to end for the title.

    **Creating a project is this call with a name no row carries yet**,
    and that is why nothing here checks the name against a list. The row
    stores a name and `board_records.store_item` mints the project id the
    first time it sees one, so there is still no second document to keep in
    step. The bounds are `set_row_project`'s, kept: `refuse_cell`'s three
    characters, plus a `*` (unbalanced emphasis does not stop at the cell in
    his file) and 40 characters.

    **Written to the #203 record store, not to his markdown** -- the fifth
    of the app's board writers off the file, after `set_priority`,
    `comment_on_row`, `edit_row` and `archive_row`. One `change_row` over the
    `project` key; the milestone name stays, as the markdown cell did, and
    now resolves under the new project.

    A row in the finished table is refused, as `set_row_project` refused
    it: the `## Done` view has no Project column (`board_view.DONE_COLUMNS`),
    so the change would land in the record and show nowhere. A missing row
    answers with `edit_row`'s 409 phrase, decided off the read for
    `edit_row`'s reason.

    **No retry**, for `set_priority`'s reason. `store` is for tests, looked
    up at call time.
    """
    board = RECORD_BOARDS.get(target)
    if board is None:
        return False, f"unknown target: {target!r}"
    name = (project or "").strip()
    refused = board_write.refuse_cell(name, "the project")
    if refused is None and "*" in name:
        refused = "the project carries a '*', which is emphasis in his file"
    if refused is None and len(name) > 40:
        refused = "the project is longer than 40 characters"
    if refused:
        return False, f"could not move #{number} on {target}: {refused}"
    store = store or board_store
    try:
        before = board_records.contents(board, store=store)
    except Exception as problem:  # noqa: BLE001 -- any failure is "not written"
        log(f"nova-capture could not read the {target} records: {problem}")
        return False, f"could not read {target}: {problem}"
    row = next(
        (item for item in before["items"] if item.get("number") == number), None)
    if row is None:
        return False, f"#{number} is not a row on {target}"
    if row.get("done"):
        return False, (f"#{number} is in the finished table on {target}, "
                       "which has no Project column")
    try:
        board_write.change_row(board, number, {"project": name}, store=store)
    except (board_write.WriteRefused, board_write.BoardDamaged,
            board_records.RecordError) as problem:
        log(f"nova-capture failed moving #{number} on {target}: {problem}")
        return False, f"could not write to {target}: {problem}"
    log(f"nova-capture moved #{number} on {target}")
    return True, f"#{number} moved on {target}"


def remove_row(target, number, store=None):
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

    **Written to the #203 record store, not to his markdown** -- the sixth and
    last of the app's board writers off the file, and the last caller of the
    markdown read-modify-write loop, which is deleted with it. One
    `board_write.remove_row`: the row's document is deleted on the revision it
    was read at, and the rest of the board is checked to come back untouched.
    The archived text is drawn by `board_view` from the record as it was read:
    the row line at the full `BOARD_COLUMNS` width (so an `ideas.md` row, whose
    generated table stops at `Milestone`, carries one empty trailing cell here)
    and the `### #N —` write-up. A finished-table row can still be deleted, as
    before.

    A missing row answers with `edit_row`'s 409 phrase, decided off a read
    for `edit_row`'s reason: `WriteRefused` also means "moved under you",
    which must stay a 502. **No retry**, for `set_priority`'s reason. `store`
    is for tests, looked up at call time.
    """
    board = RECORD_BOARDS.get(target)
    if board is None:
        return False, f"unknown target: {target!r}"
    store = store or board_store
    try:
        before = board_records.contents(board, store=store)
    except Exception as problem:  # noqa: BLE001 -- any failure is "not written"
        log(f"nova-capture could not read the {target} records: {problem}")
        return False, f"could not read {target}: {problem}"
    if not any(item.get("number") == number for item in before["items"]):
        return False, f"#{number} is not a row on {target}"
    try:
        item, write_up = board_write.remove_row(board, number, store=store)
    except board_write.RowGone:
        # A second delete of the same row lost to the first: the row he asked
        # to delete is gone, so this is the page's cue to re-read, not a 502.
        return False, f"#{number} is not a row on {target}"
    except (board_write.WriteRefused, board_write.BoardDamaged,
            board_records.RecordError) as problem:
        log(f"nova-capture failed deleting #{number} on {target}: {problem}")
        return False, f"could not write to {target}: {problem}"
    log(f"nova-capture deleted #{number} on {target}")
    text = board_view.render_row(item)
    if write_up:
        text += "\n\n" + board_view.render_detail(
            number, item.get("title"), write_up)
    _archive_deleted_row(target, number, text)
    return True, f"#{number} deleted on {target}"


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


def comment_on_row(target, number, comment, dated, author="Edvard", store=None):
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

    **Written to the #203 record store, not to his markdown** -- the second
    of the app's board writers off the file, after `set_priority`. It is
    `board_write.append_note`, which is `append_detail_note`'s records half:
    the same one-line refusals, the same `NOTE_AUTHORS` check, the note at the
    end of the write-up, and the row's `updated` cell stamped with `dated` in
    the same write.

    A `NoteRefused` comes back as `"#N is not a row on <target>: <why>"`. The
    phrase is load-bearing: `_post_board_comment` answers 409 on it -- "nothing
    there, do not retry" -- and 502 on anything else, and the markdown version
    said exactly that for a missing row *and* for a row with no write-up,
    because `append_detail_note` returned `None` for both. `append_note` checks
    the write-up before the row, so a missing row's reason reads "has no
    write-up" too; the 409 is right either way and the words are only
    approximately so.

    **No retry**, for `set_priority`'s reason: a row is its own document now,
    so the only collision left is somebody writing this same row in between,
    which `change_row` refuses without writing. That refusal is a plain
    `WriteRefused` and deliberately does **not** get the phrase: a tap again
    would work, so it is a 502 rather than a 409 telling the page there is
    nothing there.

    `store` is for tests, looked up at call time like `set_priority`'s.
    """
    board = RECORD_BOARDS.get(target)
    if board is None:
        return False, f"unknown target: {target!r}"
    store = store or board_store
    try:
        board_write.append_note(
            board, number, comment, dated, author=author, store=store)
    except board_write.NoteRefused as problem:
        log(f"nova-capture refused a comment on #{number} on {target}: {problem}")
        return False, f"#{number} is not a row on {target}: {problem}"
    except (board_write.WriteRefused, board_write.BoardDamaged,
            board_records.RecordError) as problem:
        log(f"nova-capture failed commenting on #{number} on {target}: {problem}")
        return False, f"could not write to {target}: {problem}"
    log(f"nova-capture commented on #{number} on {target}")
    return True, f"#{number} commented on on {target}"


#: The app's two board targets, named the way the record store names them.
#: Spelled here rather than imported from `tools.board_put`: the site image
#: copies `agora_runner/` and not `tools/`, so that import is green in tests
#: and an ImportError on the pod (the same call `nova_site._RECORD_BOARDS`
#: makes).
RECORD_BOARDS = {"issues": "issue", "ideas": "idea"}


#: Who may place a row. The first is the owner, through the app -- his drag
#: and his arrows; `Nova` is a cycle, which is the reprioritise run calling
#: the same route.
ROW_ORDER_AUTHORS = ("Edvard", "Nova")


def set_row_order(target, number, position, author, store=None):
    """Place one boarded row at `position` inside its milestone. Returns (ok, message).

    **A row he placed is his** (issue #202: *"A position or rating he set is
    recorded as his, and a cycle may not overwrite it"*). A placement by the
    owner stamps `placedBy` with his author value on the row it moves, and a
    placement by `Nova` of a row carrying that stamp is refused before
    anything is written. Rows a placement merely reseats keep whatever stamp they had.
    A cycle may still move *other* rows past his: that changes his row's
    seat number, never its order relative to the rows he placed, and
    forbidding it would freeze every milestone he has touched.

    Part 3 of `row-order-and-priority-migration.md`: *"make another cycle
    remove the old priority system and order the tasks in the correct new
    order and also adding functionality for me to change it."* The markdown
    half shipped in #918 and nothing called it, so the Order cell existed and
    he had no way to write one.

    **Written to the #203 record store, not to his markdown** -- the seventh
    of the app's board writers off the file, after `set_priority`,
    `comment_on_row`, `edit_row`, `archive_row`, `set_project` and
    `remove_row`. The seats are `nova_boards.row_order_seats`, where the rule
    and every refusal are documented: the first placement numbers the whole
    (project, milestone) group, seeded by rating.

    **One `change_row` per row whose seat moves, and every refusal is decided
    before the first of them.** Seats are `nova_boards.sparse_row_order_seats`,
    spaced apart, so an ordinary move in a placed group writes the moved row
    and nothing else (#202, "moving one task writes one document"); only a
    group's first placement, or one whose gap is used up, writes every row.
    A group is several documents, so there is no
    revision spanning the writes; what survives of the old single-put promise
    is that a placement naming a missing row, a closed row or a position
    outside the group writes nothing at all. A row already in its seat is not
    rewritten. If a write fails part-way the message says how many landed --
    the worst a half-finished group can show is two rows on one seat, or out
    of order, and the next placement renumbers a group in that state.

    **`position` counts the whole milestone, both boards** (issue #202): the
    seed seated each group across issues and ideas, so both boards are read
    and a move can rewrite seats on the board the row is not on. Either
    board unreadable is "not written" -- a seat computed from one board would
    collide with the other's.

    A missing row answers with `edit_row`'s 409 phrase, decided off the read.
    **No retry**, for `set_priority`'s reason. `store` is for tests, looked
    up at call time.
    """
    board = RECORD_BOARDS.get(target)
    if board is None:
        return False, f"unknown target: {target!r}"
    if author not in ROW_ORDER_AUTHORS:
        return False, f"author must be one of {ROW_ORDER_AUTHORS}, not {author!r}"
    store = store or board_store
    boards = {}
    for name, each in RECORD_BOARDS.items():  # issues first: a tie's order
        try:
            boards[each] = board_records.contents(each, store=store)["items"]
        except Exception as problem:  # noqa: BLE001 -- any failure is "not written"
            log(f"nova-capture could not read the {name} records: {problem}")
            return False, f"could not read {name}: {problem}"
    moved = next(
        (item for item in boards[board] if item.get("number") == number), None)
    if moved is None:
        return False, f"#{number} is not a row on {target}"
    if author != "Edvard" and moved.get("placedBy") == "Edvard":
        return False, (f"#{number} on {target} was placed by Edvard, and a "
                       "cycle may not move it")
    seats = _row_order_seats(boards, board, number, position)
    if seats is None:
        return False, f"cannot place #{number} on {target} at {position!r}"
    held = {(each, item["number"]): item
            for each, items in boards.items() for item in items}
    moves = []
    for each, row, seat in seats:
        was = held.get((each, row)) or {}
        changes = {}
        if was.get("order") != seat:
            changes["order"] = seat
        if (author == "Edvard" and (each, row) == (board, number)
                and was.get("placedBy") != "Edvard"):
            changes["placedBy"] = "Edvard"
        if changes:
            moves.append((each, row, changes))
    # The moved row first: its guard is the one that can refuse, and a
    # refusal before any seat lands writes nothing at all.
    moves.sort(key=lambda move: (move[0], move[1]) != (board, number))
    for written, (each, row, changes) in enumerate(moves):
        # A cycle's move re-checks his stamp on `change_row`'s own read, which
        # its compare-and-swap ties to the write -- so a drag of his landing
        # after the boards were read above still wins.
        expect = ({"placedBy": None}
                  if author != "Edvard" and (each, row) == (board, number)
                  else None)
        try:
            board_write.change_row(each, row, changes, store=store,
                                   expect=expect)
        except (board_write.WriteRefused, board_write.BoardDamaged,
                board_records.RecordError) as problem:
            log(f"nova-capture failed placing #{number} on {target}: {problem}")
            said = str(problem)
            if written:
                # `change_row` words a row that vanished mid-group with the
                # site's 409 phrase, and a 409 tells the page nothing was
                # written -- false once a seat has landed, so reword it.
                said = said.replace("is not a row", "was no longer a row")
            return False, (f"could not write to {target}: {said} -- "
                           f"{written} of {len(moves)} seat(s) were written")
    log(f"nova-capture placed #{number} on {target} at {position}")
    return True, f"#{number} is now #{position} in its milestone"
