"""Nova's own two board files, read as a board — the half that stays markdown.

Split out of `nova_site.board_payload` for issue #203, and the split is
the whole point of the module rather than tidiness. That function makes
three `parse_board` calls and only one of them is the owner's: his takes
`nova_sources.edvard_board_markdown` and converts to
`board_records.contents` like every other reader on the switchover, while
the other two take `nova_sources.nova_board_markdown` — my own live board
and its roll archive — and **there is nothing in the store to convert them
to.** `board_document.BOARDS` is the two boards the owner keeps,
`document_id` mints `board:issue:<n>` in that one id space, and
`board_migrate` never migrates my files.

`tools.board_reader_inventory` greps a module for the name, so a module
holding both kinds of call could not leave the count however much of it
was converted, and `nova_site` was that module. Exempting `nova_site`
would have excused the owner-side read too — a real conversion nobody
would then have been waiting for. So the seam goes here: this module takes
the `NOT_A_BOARD` entry beside `roll_health` and `board_row`, for the same
reason they have one, and `nova_site` becomes an ordinary reader with one
door left.

**What this deliberately does not do is render.** `board_payload` renders
the notes with `nova_site.render_blocks` and splits the write-ups with
`nova_site._split_details`, and both stay there: importing `nova_site`
from here would be a cycle, and the choice of what the *page* draws is not
a property of my board files. This returns the parsed text and nothing
else.
"""

from .nova_boards import parse_board, parse_notes


def own_board(nova_markdown, nova_archive_markdown):
    """`(board, notes)` for my own board, live file merged with its archive.

    `board` is what `parse_board` returns over my live file, with the
    archived half of each write-up glued in front of the live half — so
    `board["items"]` is the live rows and `board["details"]` carries both
    halves of every body. `notes` is the raw note stream, still carrying
    `text`: the caller renders it, because what the page draws is not a
    property of these two files.
    """
    # My own two files get parsed as a board as well as a note stream --
    # issue #97, the half of it the owner kept: *"making your board like
    # mine and giving yourself more tidiness is an improvement"*. Until
    # then `parse_board` was only ever pointed at his file, so my side of
    # the page could only ever be a flat bullet list and there was no way
    # to say a thing I filed was open, rated, or finished.
    #
    # The notes stream is untouched and stays underneath. That is the
    # design rather than a step on the way to migrating it: 654 issue
    # bullets and 221 idea bullets are a log, and a board built out of all
    # of them would be a worse board than none. A cycle boards the few
    # that are real work; the rest stay history.
    mine = parse_board(nova_markdown)
    # A write-up that has been rolled into the archive is still that
    # row's write-up. Nothing moves a `# Details` body out of the live
    # file yet -- `tools/roll_captures.py` moves the older *captures* and
    # stops there -- and that is precisely the order this has to happen
    # in: `mine` is the live file only, so the day the roller learns to
    # move a body, every row it moved would draw an empty write-up on the
    # page with nothing failing anywhere. The page has to be able to read
    # a rolled body before the roller may write one.
    #
    # A number in both files is one write-up in two halves, not two
    # versions of one, so both are drawn -- archived half first. A
    # `# Details` body is append-only: the row's original statement sits
    # at the top and every later cycle adds a `**Nova, <date> (Cycle
    # N):**` paragraph under it. So the older half is not superseded by
    # the newer one, and the archive is by construction the older.
    #
    # This used to be `setdefault` -- live wins, archived half dropped --
    # and that is what stops an *open* row's write-up from ever being
    # rolled: the roller moves the older paragraphs off, the row is
    # written again within the hour, and the moved half silently
    # disappears from the page. Only a done row could be rolled, and
    # those are 1 of 29 (`tools.roll_health`, 2026-09-06), which is why
    # 48,118 bytes of open-row bodies sit unbounded on my own issues.md.
    #
    # `parse_board` over an archive that has no `## Board` table returns
    # no items, so this adds bodies and never rows -- an archived row is
    # still a row on the live board.
    for number, body in parse_board(nova_archive_markdown)["details"].items():
        live_body = mine["details"].get(number)
        if live_body is None:
            mine["details"][number] = body
        else:
            mine["details"][number] = body.rstrip() + "\n\n" + live_body.lstrip()
    # Live first, then the rolled-off older half -- both files are
    # newest-first and the archive holds only what is older than the live
    # file's oldest, so appending preserves the order rather than
    # requiring a sort. `parse_notes` is deliberately run twice instead
    # of over a concatenation; `nova_sources.nova_board_markdown` says why.
    notes = parse_notes(nova_markdown) + parse_notes(nova_archive_markdown)
    return mine, notes
