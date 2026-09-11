"""Draw his board file from the #203 records and put it in the vault.

Issue #203 made the records his board and his `issues.md` / `ideas.md` a
generated view of them. `tools.board_publish --publish` draws that view by
hand (Cycle 1396); this is the same trip moved where the site can run it,
because the site is the process that writes the records every time he taps
a button, and a view a cycle has to remember to redraw falls behind at his
first tap. `tools/` is not in the site image, so the round-trip check the
publish refuses on -- `differences`, `layout_differences` -- lives here now
and `tools.board_migrate` / `tools.board_publish` import it back.

**`publish` takes the vault as two functions** because the two callers
reach it differently: the site through `agora_runner.vault`, a cycle on the
bridge pod through `/app/bridge/vault_tool.py` (`agora_runner.vault`
answers 401 there). `read(path)` returns `(text, rev)`; `write(path, text,
rev)` returns `(ok, detail)` and must refuse a revision that has moved.

**The publisher does nothing until `start` is called**, and only the site's
`main` calls it. `request` is reached from `nova_site.invalidate`, which
dozens of tests call, and a worker that started itself on the first
request would have those tests drawing his real board into the real vault.
"""

import collections
import threading

from agora_runner import (
    board_document, board_records, board_store, board_view, nova_boards)
from agora_runner.nova_boards import BOARD_PATHS

#: Board name (`board_document.BOARDS`) -> his vault path. Read out of
#: `BOARD_PATHS` so a moved file cannot be moved in one place only.
VAULT_PATHS = {
    "issue": BOARD_PATHS["issues"]["edvard"],
    "idea": BOARD_PATHS["ideas"]["edvard"],
}

#: Seconds of quiet after the last write before a board is drawn. A publish
#: reads and rewrites a 700-900KB document, and he taps several buttons in
#: a row, so one publish per burst rather than one per tap. Not a measured
#: number: short enough that the file is current before he could open it.
DEBOUNCE_SECONDS = 5.0

#: Consecutive failed publishes of one board before the worker stops
#: re-queueing it. A lost race re-reads and is expected to land next time;
#: one that keeps failing is a bug, and retrying it forever writes a log
#: line every five seconds for as long as the pod lives.
MAX_RETRIES = 3


def frontmatter_of(markdown):
    """The `---` block at the top of a board file, verbatim, or `""`.

    Taken off the live document because it is his, it carries the
    `contract:` line each board file explains itself with, and nothing in
    the store holds it.
    """
    lines = (markdown or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[:index + 1])
    return ""


def word_delta(before, after):
    """`(added, dropped)` word counts between two documents.

    Multisets, not sets: a word that appears four times in his archive and
    once in the render has lost three, and a set difference reports zero.
    """
    one = collections.Counter((before or "").split())
    two = collections.Counter((after or "").split())
    return sum((two - one).values()), sum((one - two).values())


def _differing_keys(one, two):
    """The field names two row dicts disagree on, sorted."""
    return sorted(
        key for key in set(one) | set(two) if one.get(key) != two.get(key))


def differences(want, got):
    """The first disagreement per key between two parsed board shapes.

    One line per key rather than a full diff: `items` is four hundred rows
    and a dump of both is unreadable, while *which field of which row* is
    the whole finding. Every key is checked -- a mismatch on `items` must
    not hide one on `captures`, because those are the two that broke
    separately during the seed.
    """
    from .board_document import OPTIONAL_FIELDS
    problems = []
    for key in ("captures", "captureReplies", "items", "details"):
        left, right = want.get(key), got.get(key)
        if key == "items" and isinstance(right, list):
            # His markdown has no cell for these, so the view can never carry
            # them: a row he placed is not drift.
            right = [{k: v for k, v in item.items() if k not in OPTIONAL_FIELDS}
                     if isinstance(item, dict) else item for item in right]
        if left == right:
            continue
        if isinstance(left, dict) and isinstance(right, dict):
            missing = sorted(set(left) - set(right))
            extra = sorted(set(right) - set(left))
            if missing or extra:
                problems.append(
                    f"{key}: the markdown has {missing[:5]} the store does "
                    f"not, the store has {extra[:5]} the markdown does not")
                continue
            for number in sorted(left):
                if left[number] != right[number]:
                    problems.append(f"{key}[{number}] differs")
                    break
            continue
        if len(left) != len(right):
            problems.append(
                f"{key}: {len(left)} from the markdown, "
                f"{len(right)} from the store")
            continue
        for index, (one, two) in enumerate(zip(left, right)):
            if one == two:
                continue
            if isinstance(one, dict) and isinstance(two, dict):
                fields = _differing_keys(one, two)
                problems.append(
                    f"{key}[{index}] (row #{one.get('number')}) differs on "
                    f"{fields}: markdown "
                    f"{ {f: one.get(f) for f in fields} } vs store "
                    f"{ {f: two.get(f) for f in fields} }")
            else:
                problems.append(
                    f"{key}[{index}] differs: markdown {one!r} "
                    f"vs store {two!r}")
            break
    return problems


def _head(block):
    """A layout block named, not dumped. Its kind, its first line, its size.

    One differing block printed whole can be a single 221KB line -- block
    200 of his `issues.md` is his entire `## Processed captures` archive --
    and the finding is *which* block moved. A table block carries no
    markdown at all (its rows live in the row documents), so it is named by
    its columns: the generic line would read `0 char(s), starting ''` on
    both sides of a real disagreement (measured 2026-09-10, his `ideas.md`
    with eight columns against `board_view`'s nine).
    """
    if not isinstance(block, dict):
        return repr(block)[:120]
    columns = block.get("columns")
    if columns is not None:
        return f"{block.get('kind')!r} block, columns {list(columns)}"
    text = str(block.get("markdown") or "")
    first = text.splitlines()[0] if text.splitlines() else ""
    return (f"{block.get('kind')!r} block, {len(text)} char(s), "
            f"starting {first[:80]!r}")


def layout_differences(markdown, board, stored):
    """Does the stored layout still match the one this markdown produces?

    Asked separately from `differences` because neither side of that
    comparison can see a layout at all -- both are written in the parser's
    four keys -- so folding it in would agree about his `## Processed
    captures` archive whether it survived or not. Compared through
    `to_layout_document`, the one spelling of a stored layout, because
    `document_layout` keeps a table header in a tuple and JSON has none.

    **The stored layout is a memory of the document at migration, and
    `board_view._laid_out` is allowed to move away from it in exactly four
    ways**, each of which this accepts and nothing else: a write-up for a
    row boarded since (a `detail` block whose number the store never named,
    and only in the seat `_laid_out` gives it, after the last stored one),
    a write-up since deleted (a stored `detail` the markdown no longer
    carries), a `## Done` table left out while no row is done, and a table
    widened by columns appended on the right (`board_width` adds `Order`
    the day a row carries a position). Until
    Cycle 1401 this compared the two lists for equality, so the first row
    boarded after the flip and the first seat written by #202 each made
    every publish of that board refuse (issue #210; the Order column on
    `ideas.md`). A detail skipped here is not unchecked: whether every
    write-up in the records came back is `differences`' `details` key.
    """
    if stored is None:
        return [f"layout: the store holds no layout for {board}"]
    wanted = board_document.layout_blocks_of(
        board_document.to_layout_document(
            board_view.document_layout(markdown), board))
    named = {int(b["number"]) for b in stored if b.get("kind") == "detail"}
    drawn = {int(b["number"]) for b in wanted if b.get("kind") == "detail"}
    # `_laid_out` writes a new write-up straight after the last stored one
    # (at the end when there is none), so that is the only seat it may take.
    seats = [i for i, b in enumerate(stored) if b.get("kind") == "detail"]
    seat = seats[-1] + 1 if seats else len(stored)
    done_drawn = any(b.get("kind") == "done" for b in wanted)
    one_at, two_at = 0, 0
    while one_at < len(wanted) or two_at < len(stored):
        one = wanted[one_at] if one_at < len(wanted) else None
        two = stored[two_at] if two_at < len(stored) else None
        if one is not None and two is not None and (
                one == two or _widened(one, two)):
            one_at, two_at = one_at + 1, two_at + 1
        elif two_at >= seat and _detail_not_in(one, named):
            one_at += 1
        elif _detail_not_in(two, drawn):
            two_at += 1
        elif (isinstance(two, dict) and two.get("kind") == "done"
              and not done_drawn):
            two_at += 1  # `## Done` is left out while no row is done
        else:
            return [f"layout[{one_at}] differs: markdown {_head(one)} "
                    f"vs store {_head(two)}"]
    return []


def _detail_not_in(block, numbers):
    """Is `block` a write-up whose row number is not in `numbers`?"""
    return (isinstance(block, dict) and block.get("kind") == "detail"
            and int(block["number"]) not in numbers)


def _widened(one, two):
    """Is `one` the table block `two` with columns appended on the right?

    Appended only: a renamed, dropped or reordered header is a different
    table, and `parse_board` reads cells by position.
    """
    new, old = one.get("columns"), two.get("columns")
    if new is None or old is None or one.get("kind") != two.get("kind"):
        return False
    rest = {k: v for k, v in one.items() if k != "columns"}
    return (len(new) > len(old) and list(new[:len(old)]) == list(old)
            and rest == {k: v for k, v in two.items() if k != "columns"})


def reread(text):
    """The board a rendered view reads back as, in the parser's four keys.

    The one markdown parse in this module, and it is of the text `render`
    just drew -- never of a source. It is the check, not a reader: a view
    is faithful when it re-reads as the records it came from.
    """
    parsed = nova_boards.parse_board(text)
    return parsed if isinstance(parsed, dict) else {}


def render(board, markdown, store=board_store):
    """`(text, problems)` -- his board drawn from the records.

    `problems` is empty when the render re-reads as the records it came
    from. `markdown` is the live document and is used for two things only:
    its frontmatter, which the store does not hold, and the word delta.
    """
    contents = board_records.contents(board, store=store)
    layout = store.read_layout(board)
    text = board_view.render_document(
        contents, frontmatter=frontmatter_of(markdown), layout=layout)
    problems = differences(contents, reread(text))
    problems += layout_differences(text, board, layout)
    return text, problems


def publish(board, read, write, store=board_store):
    """Draw `board` from the records, write it to the vault, stamp it.

    Returns `(code, lines)`: 0 published or already current, 2 refused
    before anything was written, 3 the vault write or its read-back
    failed. The stamp is the LAST step and is only taken on a read-back
    that matches what was drawn -- a stamp is a claim about what the vault
    holds, and one taken on a write that lost a race would certify the
    other writer's text.
    """
    path = VAULT_PATHS[board]
    lines = [f"board: {board} -> {path}"]
    markdown, rev = read(path)
    if markdown is None or not rev:
        return 2, lines + ["REFUSED: could not read the live file and its "
                           "revision; nothing was written"]
    try:
        text, problems = render(board, markdown, store=store)
    except board_records.UnmigratedStore as exc:
        return 2, lines + [f"REFUSED: {exc}"]
    if problems:
        return 2, lines + [f"  {p}" for p in problems] + [
            "REFUSED: the rendered document does not re-read as the records "
            "it was drawn from; nothing was written"]
    added, dropped = word_delta(markdown, text)
    lines.append(f"words: +{added} / -{dropped}")
    if text == markdown:
        board_records.stamp_source_rev(board, rev, store=store)
        return 0, lines + [f"unchanged: the vault already holds this view; "
                           f"stamped {rev}"]
    ok, detail = write(path, text, rev)
    if not ok:
        return 3, lines + [detail or "",
                           "FAILED: the vault write did not land; the records "
                           "were not stamped"]
    landed, new_rev = read(path)
    if landed != text or not new_rev:
        return 3, lines + ["FAILED: the vault does not read back as the view "
                           "just written; the records were not stamped"]
    board_records.stamp_source_rev(board, new_rev, store=store)
    return 0, lines + [f"published: {len(text)} bytes, vault {rev} -> "
                       f"{new_rev}, records stamped {new_rev}"]


class Publisher:
    """One thread that redraws a board a few seconds after it was written.

    `request` only marks a board and wakes the thread, so a button's
    response never waits on a 900KB vault round trip. Requests landing
    while one is pending coalesce, and `drain` publishes each board once.
    """

    def __init__(self, read, write, log=print, debounce=DEBOUNCE_SECONDS,
                 store=board_store):
        self.read, self.write, self.log = read, write, log
        self.debounce, self.store = debounce, store
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.pending = set()
        self.failures = collections.Counter()

    def request(self, board):
        with self.lock:
            self.pending.add(board)
        self.wake.set()

    def drain(self):
        """Publish every pending board once; re-queue a failure, bounded."""
        with self.lock:
            boards, self.pending = sorted(self.pending), set()
        for board in boards:
            try:
                code, lines = publish(board, self.read, self.write,
                                      store=self.store)
            except Exception as problem:  # noqa: BLE001 -- never kill the thread
                code, lines = 1, [f"board: {board}", f"ERROR: {problem!r}"]
            self.log("board_publish " + " | ".join(lines))
            if code == 0:
                self.failures.pop(board, None)
                continue
            self.failures[board] += 1
            if self.failures[board] < MAX_RETRIES:
                self.request(board)
            else:
                self.log(f"board_publish {board}: gave up after "
                         f"{self.failures[board]} failed attempt(s); the next "
                         f"write to that board tries again")
                self.failures.pop(board, None)

    def run(self):
        while not self.stopped.is_set():
            self.wake.wait()
            # Debounce: keep waiting while writes keep arriving.
            while not self.stopped.is_set():
                self.wake.clear()
                if not self.wake.wait(self.debounce):
                    break
            if not self.stopped.is_set():
                self.drain()

    def stop(self):
        """End `run` -- for tests; the site's thread lives as long as the pod."""
        self.stopped.set()
        self.wake.set()


_publisher = None


def start(read, write, log=print, debounce=DEBOUNCE_SECONDS):
    """Start the one publisher thread. The site's `main` is the only caller."""
    global _publisher
    if _publisher is not None:
        return _publisher
    _publisher = Publisher(read, write, log=log, debounce=debounce)
    threading.Thread(target=_publisher.run, name="board-publish",
                     daemon=True).start()
    return _publisher


def request(board):
    """Ask for `board` to be redrawn. A no-op until `start` has run."""
    publisher = _publisher
    if publisher is None or board not in VAULT_PATHS:
        return False
    publisher.request(board)
    return True
