"""The idea pool: ten candidates I generated, waiting on one tap each.

The owner, `ideas.md` #92: *"the thing that sparkes this idea is to also
have a list of ideas that you have generated per project, and i can
approve or comment on these. And lets say the idea list should always be
a list of 10 new ideas, so when a idea gets approved (added to the Kanban
backlog) or rejected (added to discarded ideas pile as we need to keep
track of discarded ideas), new ones are generated in their place."*

This is phase 1 of `nova/resources/ideas/project-dashboard-and-idea-pool.md`,
which is the whole design and is decided rather than open. Everything on
either board today exists because he typed it, or because a cycle boarded
something he typed. The pool is the first mechanism that runs the other
way: I propose, he decides with one tap, and steering what I work on
stops costing him a written capture.

**Never a model call from here.** `nova_site` has no Claude access and
must not get one -- rule 9, production never spends the metered API. So
"generate on demand" is a *request flag* in the pool's frontmatter, not a
synchronous generation: the button sets it, and the next cycle to read the
pool fills it and clears it. Nothing about a page load may ever become a
model call, and that boundary is permanent rather than a phase-1
shortcut.

**A decided candidate leaves the pool, and that is what stops this
becoming another list nobody reads.** The old asks block died of exactly
the opposite property: a shared list rewritten every cycle by an
author who had not written the items in it, where keeping something cost
nothing and removing it required being sure. Here approve and reject both
*remove*, so the pool cannot silently accumulate.

**Write to his file first, then remove from the pool.** Two vault
documents with two revisions and no transaction between them, so one of
the two half-done states has to be chosen deliberately -- the same call
`nova_capture.convert_capture` makes, for the same reason. Removing first
and failing the second write loses a decision he made. Writing first and
failing the removal leaves a candidate in the pool he will see again and
can decide again, which is recoverable in one tap.
"""

import re

from agora_runner.log import log
from agora_runner.nova_boards import canonical_priority, parse_board, priority_key
from agora_runner.vault import vault_read_path_rev, vault_write_path

POOL_PATH = "projects/sokrates/projects/agora/nova/resources/idea-pool.md"

# Approve and reject both land here -- his vault, his phone, the file he
# already reads. The pool is disposable by construction; the record of a
# decision is not, so it goes where the durable things are. Imported from
# `nova_capture` rather than respelled would be a circular import; this is
# the same constant and `test_pool_targets_his_ideas_file` pins them equal.
IDEAS_PATH = "projects/sokrates/projects/nova/ideas.md"

WRITE_ATTEMPTS = 3

# What `decide` answers when the candidate at `index` is not the one the
# page was showing. Named because `nova_site` matches on it to pick 409
# over 502, the same way `nova_capture.STALE_CAPTURE` is.
STALE_CANDIDATE = "no longer in the pool"

_FRONTMATTER_FLAG = re.compile(r"^generate-requested:\s*(.*)$", re.MULTILINE)
_FIELD = re.compile(r"^(project|priority):\s*(.*)$", re.IGNORECASE)

# The one sentence that says a write-up came from the pool rather than from
# something he typed, and the only thing `decision_history` can key on. It is
# a constant because `insert_detail` writes it and the history parser reads
# it: two hand-copies of one sentence is the duplication this loop keeps
# shipping guards for instead of deleting.
APPROVED_BYLINE = "Proposed by Nova and approved by you on {dated}."
_APPROVED_BYLINE = re.compile(
    r"^Proposed by Nova and approved by you on (.+?)\.\s*$")

# `## 115 — Title`, which is what `insert_detail` writes, and `### #114 —
# Title`, which is what his older hand-written write-ups look like. The
# history has to read both or it reports pool approvals as missing on a file
# whose older half simply predates the pool.
_DETAIL_HEADING = re.compile(r"^#{2,4}\s+#?(\d+)\s*[—–-]\s*(.+?)\s*$")

# What `decide` appends when he typed something into the comment box.
_YOU_SAID = re.compile(r"^You said:\s*(.+?)\s*$")


def _frontmatter_span(lines):
    """(start, end) of the frontmatter body, or (0, 0) if there is none."""
    if not lines or lines[0].strip() != "---":
        return 0, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 1, i
    return 0, 0


def parse_pool(markdown):
    """The pool document -> `{candidates: [...], generateRequested: bool}`.

    A candidate is one `## ` heading with a `project:` line, a `priority:`
    line and a body. The two fields are read by name anywhere in the
    block rather than by line offset, because the refill writes this file
    by hand on a heartbeat and a fixed offset is a rule only a machine
    keeps.

    `index` is the candidate's position in this list and is only half of
    an address -- `title` is the other half, and the two are checked
    together on the way in. Position alone is not safe: a refill that runs
    while the page is open renumbers everything below it.
    """
    lines = (markdown or "").split("\n")
    _, fm_end = _frontmatter_span(lines)
    requested = False
    if fm_end:
        flag = _FRONTMATTER_FLAG.search("\n".join(lines[:fm_end]))
        if flag:
            requested = flag.group(1).strip().lower() in ("yes", "true", "1")

    candidates = []
    current = None
    for line in lines[fm_end:]:
        if line.startswith("## "):
            if current:
                candidates.append(current)
            current = {"title": line[3:].strip(), "project": "", "priority": "", "body": []}
            continue
        if current is None:
            continue
        field = _FIELD.match(line.strip())
        # Only before any body *text*: a `priority:` line halfway down a
        # paragraph is prose, not a field. Blank lines do not count, because
        # every candidate in the pool has one between its heading and its
        # first field -- testing `not current["body"]` instead read every
        # field as prose and left the priority empty on all ten.
        if field and not any(b.strip() for b in current["body"]):
            current[field.group(1).lower()] = field.group(2).strip()
            continue
        current["body"].append(line)
    if current:
        candidates.append(current)

    out = []
    for c in candidates:
        if not c["title"]:
            continue
        priority = canonical_priority(c["priority"]) or c["priority"]
        out.append({
            "index": len(out),
            "title": c["title"],
            "project": c["project"] or "Nova",
            "priority": priority,
            # The CSS class suffix, sent rather than re-derived in the
            # browser: `app.js` computes this for board rows inside a
            # closure the pool page cannot reach, and a second copy of the
            # mapping is the duplication that generates drift checks.
            "priorityKey": priority_key(priority),
            "body": "\n".join(c["body"]).strip(),
        })
    return {"candidates": out, "generateRequested": requested}


def find_candidate(markdown, index, title):
    """The candidate at `index`, only if it is still called `title`.

    Returns `(candidate, None)` or `(None, reason)`. The title is the match
    key for the reason `nova_capture` gives about `original`: a pool a
    refill rewrote while the page was open must be a refusal, never a
    different candidate decided by accident.
    """
    pool = parse_pool(markdown)
    candidates = pool["candidates"]
    if not isinstance(index, int) or index < 0 or index >= len(candidates):
        return None, STALE_CANDIDATE
    found = candidates[index]
    if found["title"].strip() != (title or "").strip():
        return None, STALE_CANDIDATE
    return found, None


def remove_candidate(markdown, title):
    """Drop the `## <title>` block from the pool. Returns the new markdown.

    Matched on the heading text rather than on an index, because the index
    was already checked by `find_candidate` and re-deriving it here would
    be the same lookup done twice with two chances to disagree.
    """
    lines = (markdown or "").split("\n")
    want = (title or "").strip()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip() == want:
            start = i
            break
    if start is None:
        return markdown
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[:start] + lines[end:])


def set_generate_flag(markdown, requested):
    """Set `generate-requested:` in the frontmatter. Returns the new markdown.

    The flag lives in frontmatter rather than in a heading because a cycle
    reads this file with `vault_tool.py get` and the first ten lines are
    what it sees without scrolling. If the key is missing it is added as
    the last frontmatter line rather than the first, so the `contract:`
    line a cycle actually needs to read stays at the top.
    """
    value = "yes" if requested else "no"
    lines = (markdown or "").split("\n")
    fm_start, fm_end = _frontmatter_span(lines)
    if not fm_end:
        # No frontmatter at all: give it one rather than dropping the flag
        # somewhere nothing looks for it.
        return "\n".join(["---", f"generate-requested: {value}", "---"] + lines)
    for i in range(fm_start, fm_end):
        if _FRONTMATTER_FLAG.match(lines[i]):
            lines[i] = f"generate-requested: {value}"
            return "\n".join(lines)
    lines.insert(fm_end, f"generate-requested: {value}")
    return "\n".join(lines)


def next_number(ideas_markdown):
    """One past the highest row number on his ideas board.

    Reads `## Board` *and* `## Done` through `parse_board`, so a number
    that has been finished and moved is never handed out twice -- every
    journal entry, claim slug and board comment points at these numbers,
    so reuse is worse than a gap.
    """
    items = parse_board(ideas_markdown or "").get("items", [])
    numbers = [i["number"] for i in items if isinstance(i.get("number"), int)]
    return (max(numbers) + 1) if numbers else 1


def _already_boarded(ideas_markdown, title):
    """Is a row with exactly this title already on his ideas board?

    The de-duplication guard for a decision that arrives twice. Matched on
    the title because that is what the candidate carries -- the number does
    not exist yet on the second write, which is the whole problem.
    `## Done` counts too: an idea approved, finished and moved is still one
    he already said yes to, and re-boarding it would hand him a duplicate
    of something he has already closed.
    """
    want = (title or "").strip()
    if not want:
        return False
    return any(
        (item.get("title") or "").strip() == want
        for item in parse_board(ideas_markdown or "").get("items", [])
    )


def _board_table_head(lines):
    """Index just past the `## Board` table's header separator, or None.

    New rows go at the *top* of the table because that is where the board
    already keeps its newest -- #114 is the first row in his file, not the
    last -- and a row appended to the bottom would be the one place he
    never looks.
    """
    for i, line in enumerate(lines):
        if line.strip().lower() != "## board":
            continue
        for j in range(i + 1, min(i + 8, len(lines))):
            if lines[j].lstrip().startswith("|---") or re.match(r"^\s*\|[\s:-]+\|", lines[j]):
                return j + 1
        return None
    return None


def insert_board_row(markdown, number, title, priority, dated):
    """Add a numbered row to the top of `## Board`. Returns `(markdown, error)`.

    The first cell is an Obsidian wikilink whose target is the row's
    `# Details` heading, so the `|` inside it has to be escaped as `\\|` or
    the table gains a sixth cell against five headers. Every existing row
    in his file is written this way.
    """
    lines = (markdown or "").split("\n")
    head = _board_table_head(lines)
    if head is None:
        return markdown, "could not find the ## Board table"
    link = f"#{number} — {title}"
    row = f"| [[{link}\\|{number}]] | {title} | ⚪ Backlog | {dated} | {priority} |"
    lines.insert(head, row)
    return "\n".join(lines), ""


def insert_detail(markdown, number, title, body, dated):
    """Add `## <number> — <title>` immediately under `# Details`.

    Newest first, matching the file: `## 69` sits above `## 68`. Appending
    at the bottom would put a new row's write-up below two hundred older
    ones, which is the same "he never looks there" failure as appending to
    the table.
    """
    lines = (markdown or "").split("\n")
    for i, line in enumerate(lines):
        if line.strip() == "# Details":
            block = [
                "",
                f"## {number} — {title}",
                "",
                APPROVED_BYLINE.format(dated=dated),
                "",
            ]
            if body:
                block += [body, ""]
            return "\n".join(lines[:i + 1] + block + lines[i + 1:]), ""
    return markdown, "could not find the # Details section"


def insert_discarded(markdown, title, why):
    """Add a row to `## Discarded`, which already exists in his file.

    He asked for the pile explicitly -- *"as we need to keep track of
    discarded ideas"* -- and the reason column is what stops the next
    refill re-offering something he already turned down. A discarded row
    with an empty reason is one I regenerate on Tuesday and he rejects
    again.
    """
    lines = (markdown or "").split("\n")
    head = None
    for i, line in enumerate(lines):
        if line.strip().lower() != "## discarded":
            continue
        for j in range(i + 1, min(i + 10, len(lines))):
            if lines[j].lstrip().startswith("|---") or re.match(r"^\s*\|[\s:-]+\|", lines[j]):
                head = j + 1
                break
        break
    if head is None:
        return markdown, "could not find the ## Discarded table"
    lines.insert(head, f"| {_table_cell(title)} | {_table_cell(why)} |")
    return "\n".join(lines), ""


def _table_cell(text):
    """One markdown table cell out of whatever he typed into a textarea.

    **This is a write into his vault, so getting it wrong is not recoverable
    by editing code.** The comment box is a `<textarea>`, so a reason can
    contain newlines and pipes; a raw newline ends the table mid-row and a
    raw `|` opens a third cell against a two-column header. Both leave a
    permanently malformed table in a file he reads in Obsidian, and the
    history view then shows him the fragment before the break as if it were
    the whole thing -- which is the precise opposite of what he asked for.

    Whitespace collapses rather than becoming `<br>`: a "why not" cell is a
    sentence by construction, every word survives, and nothing about the
    round trip depends on a renderer honouring an HTML tag inside a table.
    """
    return re.sub(r"\s+", " ", (text or "").strip()).replace("|", r"\|")


def _write_with_retry(path, mutate):
    """Read-modify-write one vault document, retrying only a real conflict.

    `mutate(current)` returns `(new_markdown, error)`. Same loop and same
    409-only retry as `nova_capture.capture`: anything that is not a
    conflict will fail identically next time, so retrying it just spins.
    """
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(path)
        if current is None:
            return False, f"{path} not found"
        updated, error = mutate(current)
        if error:
            return False, error
        result = vault_write_path(path, updated, if_rev=rev)
        if result == "written":
            return True, "written"
        if "409" not in result:
            break
    return False, f"could not write to {path}: {result}"


def decide(index, title, decision, comment, dated):
    """Approve or reject one candidate. Returns `(ok, message)`.

    Approve writes a numbered `## Board` row with the priority I already
    guessed, plus its `# Details` write-up. Reject writes a `## Discarded`
    row carrying his reason. Either way the candidate then leaves the
    pool, and the pool write is second on purpose -- see the module
    docstring.
    """
    if decision not in ("approve", "reject"):
        return False, f"unknown decision: {decision!r}"

    pool_markdown, _ = vault_read_path_rev(POOL_PATH)
    if pool_markdown is None:
        return False, f"{POOL_PATH} not found"
    candidate, why = find_candidate(pool_markdown, index, title)
    if candidate is None:
        return False, why

    comment = (comment or "").strip()
    if decision == "approve":
        priority = candidate["priority"] or "🔵 Medium"

        def mutate(current):
            # **Idempotent, because `_write_with_retry` is a retry loop and
            # a retry here is not the only way this runs twice.** Two taps
            # on a phone are two requests on two threads, and both pass
            # `find_candidate` before either has emptied the pool. The
            # first lands #115; the second loses the compare-and-swap,
            # re-reads, recomputes `next_number()` as 116 and boards the
            # same idea again -- and both report success, so nothing
            # anywhere says he now has two identical rows. Reviewer finding
            # on this PR. Checking the title before inserting turns the
            # second write into a no-op instead.
            if _already_boarded(current, candidate["title"]):
                return current, ""
            number = next_number(current)
            updated, error = insert_board_row(
                current, number, candidate["title"], priority, dated)
            if error:
                return current, error
            body = candidate["body"]
            if comment:
                body = (body + "\n\n" + f"You said: {comment}").strip()
            return insert_detail(updated, number, candidate["title"], body, dated)

        landed = "boarded on ideas"
    else:
        why_text = comment or f"Rejected {dated}"

        def mutate(current):
            return insert_discarded(current, candidate["title"], why_text)

        landed = "discarded"

    ok, message = _write_with_retry(IDEAS_PATH, mutate)
    if not ok:
        return False, message

    ok, removal = _write_with_retry(
        POOL_PATH, lambda current: (remove_candidate(current, candidate["title"]), ""))
    if not ok:
        # The decision landed in his file and the candidate is still in the
        # pool. Say so rather than reporting success: he will see it again
        # and the honest message is what stops him deciding it twice
        # without knowing the first one worked.
        log(f"nova-pool {decision}d {candidate['title']!r} but left it in the pool: {removal}")
        return False, f"{landed}, but it is still in the pool — decide it again to clear it"

    log(f"nova-pool {decision}: {candidate['title']!r} -> {landed}")
    return True, landed


def parse_history(ideas_markdown):
    """His ideas file -> `{"approved": [...], "rejected": [...]}`.

    The owner, capture 2026-08-25: *"I do not know if my comments on why or
    why not a pool idea is rejected or not. As in the comments i write. I
    can't even see what has been approved or rejected. Maybe give me a
    history overview."*

    He is right and the pool was built that way on purpose -- a decided
    candidate leaves the pool so the pool cannot silently accumulate -- but
    "leaves the pool" was implemented as "leaves the app". An approval lands
    as a board row he would have to recognise as his own, and a rejection
    lands in a `## Discarded` table that **nothing in this site renders at
    all**, so the reason he typed is readable only in Obsidian. One real
    rejection had already gone in that way by the time he asked.

    **Derived from his file rather than from a new ledger, and that is the
    whole design decision.** A decisions document written beside the pool
    would be a second copy of a fact his file already holds, needing its own
    write path inside `decide`'s two-document dance -- and it would start
    empty, which is the one thing this must not do: he asked to see history
    that has *already happened*. Everything below is parsed back out of the
    writes `insert_detail` and `insert_discarded` were making anyway.

    What this cannot recover, said plainly rather than papered over: a
    rejection carries no date, because `insert_discarded` writes the reason
    *instead of* the date when he typed one, into a two-column table whose
    shape is his. The rows come back in file order, which is newest first.
    """
    lines = (ideas_markdown or "").split("\n")
    return {
        "approved": _parse_approved(lines),
        "rejected": _parse_rejected(lines),
    }


def _parse_approved(lines):
    """Every `# Details` write-up carrying the pool's own byline."""
    approved = []
    number = title = None
    dated = comment = ""
    seen_byline = False
    in_comment = False

    def flush():
        if seen_byline and number is not None:
            approved.append({
                "number": number, "title": title,
                "dated": dated, "comment": comment,
            })

    for line in lines:
        heading = _DETAIL_HEADING.match(line)
        if heading:
            flush()
            number, title = int(heading.group(1)), heading.group(2)
            dated = comment = ""
            seen_byline = False
            in_comment = False
            continue
        if number is None:
            continue
        byline = _APPROVED_BYLINE.match(line.strip())
        if byline:
            seen_byline = True
            dated = byline.group(1)
            continue
        said = _YOU_SAID.match(line.strip())
        if said and seen_byline:
            comment = said.group(1)
            in_comment = True
            continue
        # **A comment can be more than one line and the box he types into is
        # a textarea.** `decide` writes `You said: <whatever he typed>`
        # verbatim into the write-up, so a two-paragraph comment is a
        # `You said:` line followed by more lines -- and reading only the
        # first one shows him less than he wrote, silently, which is the
        # failure this whole view exists to end. The write-up's own prose
        # ends at the blank line before the next heading, so that is where
        # the comment ends too.
        if in_comment:
            if not line.strip():
                in_comment = False
            else:
                comment = (comment + "\n" + line.strip()).strip()
    flush()
    return approved


def _parse_rejected(lines):
    """The rows of `## Discarded`, which no page has ever shown him."""
    rejected = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == "## discarded":
            inside = True
            continue
        if not inside:
            continue
        if stripped.startswith("#"):
            break
        if not stripped.startswith("|"):
            continue
        # Split on unescaped pipes only, then put the escaped ones back as
        # the character he typed. `_table_cell` is the other half of this.
        cells = [c.replace(r"\|", "|").strip()
                 for c in re.split(r"(?<!\\)\|", stripped.strip("|"))]
        if len(cells) < 2:
            continue
        # The header row and the `|---|---|` separator under it. Matched by
        # what they are rather than by position: the table in his file was
        # hand-written and a later one need not have the same preamble.
        if re.match(r"^[\s:-]+$", cells[0]) or cells[0].lower() == "idea":
            continue
        rejected.append({"title": cells[0], "why": cells[1]})
    return rejected


def history_payload():
    """What `GET /api/pool/history` answers.

    Its own endpoint rather than a field on `/api/pool`, because his ideas
    file is 281KB and the pool document is 5KB. Folding this into the pool
    payload would make opening the deck a 286KB read to paint a section he
    has not asked to see; the page fetches this when he taps History.
    """
    markdown, _ = vault_read_path_rev(IDEAS_PATH)
    if markdown is None:
        return {"approved": [], "rejected": [], "missing": True}
    payload = parse_history(markdown)
    payload["missing"] = False
    return payload


def request_generate():
    """Set the refill flag. Returns `(ok, message)`.

    The button he taps. It does not generate -- see the module docstring
    -- it asks, and the next cycle to read the pool answers. Idempotent:
    asking twice is the same as asking once, so a double tap on a phone
    costs nothing.
    """
    ok, message = _write_with_retry(
        POOL_PATH, lambda current: (set_generate_flag(current, True), ""))
    if not ok:
        return False, message
    return True, "asked for more candidates"


def pool_payload():
    """What `GET /api/pool` answers.

    `missing` rather than an error when the document is not there: an
    empty pool and an absent one both mean "nothing to decide", and the
    page should say that rather than showing a failure he cannot act on.
    """
    markdown, _ = vault_read_path_rev(POOL_PATH)
    if markdown is None:
        return {"candidates": [], "generateRequested": False, "missing": True}
    payload = parse_pool(markdown)
    payload["missing"] = False
    return payload
