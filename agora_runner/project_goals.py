"""Where a project's objective, its key results and its KPIs live.

Step 1 of issue #227's own sequencing -- *"the model and the storage --
where an objective, its key results and a project's KPIs live, and the
`Serves` column"* -- and nothing else. No content for any project: that is
step 2, and it is settled with him in a conversation rather than written
by me alone.

The owner approved the model in live chat on 2026-09-13: *"Yes! I want to work
like this! ... I trust you to do this correctly!"* The shape is

    Project -> Objective (agreed with him in a conversation, or struck)
            +- Key result (an outcome with a measure; 2-3 per project)
            +- Milestone (a group of work that SERVES a key result)
            +- Task (one cycle, one checkable definition of done)

and, beside the objective and never inside it, **KPIs** -- health numbers
with a range rather than a target, for the things that must stay in bounds
while the work happens.

**Why a new document and not a column on `projects.md`.** That file is one
row per project and this is two-to-three key results plus a handful of KPIs
per project, each with its own measure and numbers. The row would have to
carry a list, and a list in a markdown cell is the thing `board_records`
was built to stop. So: one document, one `## Project` section per project,
and fenced blocks inside it.

**Why fences, and why these field names.** `/plan` already renders
`goals.md`'s ```goal blocks into a scoreboard off `measure`/`now`/`target`
/`unit`/`direction`, and the issue says in as many words to reuse that
machinery rather than invent a second one. A key result is the same kind of
object with an `id` and an owner project, so it carries the same field
names and `nova_plan`'s reader would recognise it. Everything outside a
fence in this document is prose that nothing parses -- the same rule
`/plan`'s two documents hold.

**The rules the issue says are the whole point, and which of them are
mechanical here.** `problems()` refuses what can be checked by reading:

* more than three key results in one project (*"Fifteen is a backlog
  wearing a hat"*),
* a key result with no `measure` -- an outcome with no measure is a task
  with a nicer name, which is rule 1,
* a KPI carrying a `target:` -- *"the moment a guardrail carries a target,
  the dashboard gets optimised instead of the work"*,
* a KPI with no range at all (`low`/`high`, either bound is enough),
* an id used twice anywhere in the document, because `Serves` points at ids
  and an ambiguous pointer is worse than no pointer,
* an objective with no `statement`, or a status outside the three.

What is **not** mechanical, and deliberately is not faked here: whether a
key result is really an outcome rather than a task list. *"Ship the landing
page"* passes every check above and is still wrong. That judgement is
argued out between us in the objective's own conversation -- which is why
`agreed` has to link one -- and a regex pretending to make it mechanical
would only teach a cycle to phrase tasks past the regex.

**`serves_problems` is the other half of the link and it is where the
KPI/key-result boundary is actually enforced.** A milestone's `Serves` cell
names key-result ids. Pointing one at a KPI id is refused by name rather
than falling through the unknown-id branch, because that is exactly the
mistake the issue says must be impossible: a guardrail used as a goal.
"""

import re

#: One document rather than a column, for the reason in the module docstring.
#: It sits beside `projects.md`, `milestones.md` and `milestone-seats.md` in
#: his own folder, which is where every other board-shaped document I write
#: for him already lives.
PROJECT_GOALS_PATH = "projects/sokrates/projects/nova/project-goals.md"

PROJECT_GOALS_TEMPLATE = """\
---
type: board
tags: [agora, goals, board]
status: built
contract: Nova writes this. One `## <Project>` section per project, holding one ```objective fence (his words, status discussing/agreed/struck, with the Agora conversation it was agreed in), 2-3 ```key-result fences (an outcome with a measure, now and target) and any number of ```kpi fences (a health number with a range, never a target). A KPI is never a key result. Milestones point at a key result by id in the Serves column of milestone-seats.md. The set of projects that exist is read off the Project column on the boards, never from here.
---

# Project goals
"""

#: Deliberately the same vocabulary as `nova_plan`'s ```goal fence, so the
#: scoreboard machinery on `/plan` reads a key result without a second
#: dialect. `id` is the one addition and it is what `Serves` points at.
KEY_RESULT_FIELDS = (
    "id", "name", "measure", "now", "target", "unit", "direction", "status")

#: A KPI has a range, not a target. `target` is **kept** here rather than
#: dropped as an unknown key, and that is the whole point: `_fields` drops
#: what it does not recognise, so leaving `target` out would make a KPI with
#: a target parse clean and `problems()` would have nothing to refuse. It is
#: read in so it can be rejected.
KPI_FIELDS = ("id", "name", "measure", "now", "low", "high", "unit", "target")

OBJECTIVE_FIELDS = ("statement", "status", "conversation")

#: `struck` rather than `/plan`'s `declined`: he strikes an objective, and
#: the word is his. A struck objective keeps its block, the same way a
#: declined goal does -- a decision is worth being able to read back.
#:
#: `proposed`/`approved` were the first two words here and they are gone on
#: purpose, refused rather than aliased. The owner, 2026-09-13 21:02: *"I should
#: not have to approve goals. Goals, milestones, kpis and okrs should be
#: derived based on a conversation between you and me where we challenge each
#: other and then agree on something. Not where you guess something and i just
#: approve or decline."* That deletes the approval gate, so an objective is
#: either still being argued about (`discussing`) or settled between us
#: (`agreed`). A document still carrying the old words does not quietly parse
#: as the new ones: the whole correction is that an approval is not an
#: agreement, and a silent alias would say they are the same.
OBJECTIVE_STATUSES = ("discussing", "agreed", "struck")
DEFAULT_OBJECTIVE_STATUS = "discussing"

#: *"Two or three key results per project. Fifteen is a backlog wearing a
#: hat."* The floor is not checked: a project mid-proposal legitimately has
#: one, and refusing that would block the step that writes the second.
MAX_KEY_RESULTS = 3

_HEADING_RE = re.compile(r"^##[ \t]+(?P<name>.+?)[ \t]*$")
_FENCE_OPEN_RE = re.compile(r"^[ \t]*```[ \t]*(?P<name>[a-z-]+)[ \t]*$")
_FENCE_CLOSE_RE = re.compile(r"^[ \t]*```[ \t]*$")
_FIELD_RE = re.compile(r"^(?P<key>[a-z-]+):[ \t]*(?P<value>.*?)[ \t]*$")

_FENCES = {"objective": "objective", "key-result": "keyResults", "kpi": "kpis"}


def _fields(lines, allowed):
    """Body lines of one fence -> `{field: value}`, unknown keys dropped.

    Dropping rather than keeping is the same call `nova_plan._goal` makes:
    an unknown key is a typo far more often than it is a field somebody
    added, and carrying it forward would let `target` survive on a KPI
    under a misspelling that `problems()` never looks at.
    """
    out = {}
    for line in lines:
        match = _FIELD_RE.match(line.strip())
        if match and match.group("key") in allowed:
            out[match.group("key")] = match.group("value")
    return out


def parse_project_goals(markdown):
    """`project-goals.md` -> `{lowercased project: section}`.

    A section is `{"project", "objective", "keyResults", "kpis"}`. Keyed
    lowercase for `parse_project_meta`'s reason -- the name is free text he
    types on a phone and `nova` and `Nova` are one project -- and `project`
    carries the spelling actually written so a heading reads his way.

    Fences outside any `##` heading are dropped rather than filed under a
    blank project: a key result belongs to exactly one project and a
    guessed owner is worse than a missing one.
    """
    out, current, fence, body = {}, None, None, []
    for line in (markdown or "").split("\n"):
        if fence is not None:
            if _FENCE_CLOSE_RE.match(line):
                if current is not None and fence in _FENCES:
                    if fence == "objective":
                        out[current]["objective"] = _fields(
                            body, OBJECTIVE_FIELDS)
                    elif fence == "key-result":
                        out[current]["keyResults"].append(
                            _fields(body, KEY_RESULT_FIELDS))
                    else:
                        out[current]["kpis"].append(_fields(body, KPI_FIELDS))
                fence, body = None, []
            else:
                body.append(line)
            continue
        opened = _FENCE_OPEN_RE.match(line)
        if opened and opened.group("name") in _FENCES:
            fence, body = opened.group("name"), []
            continue
        if opened:
            # A fence this module does not own -- a code sample in the prose.
            # Skip to its close so its contents can never be read as fields.
            # `_FENCES` gates the write above, so `""` here files nothing;
            # before that gate existed it fell through to the `kpis` branch
            # and a ```python sample became a KPI.
            fence, body = "", []
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            name = heading.group("name")
            current = name.lower()
            out.setdefault(
                current,
                {"project": name, "objective": {}, "keyResults": [], "kpis": []})
    return out


def _ids(section):
    for row in section.get("keyResults", ()):
        yield row.get("id", "").strip().lower(), "key result"
    for row in section.get("kpis", ()):
        yield row.get("id", "").strip().lower(), "KPI"


def problems(markdown_or_sections):
    """Every rule in issue #227 that can be checked by reading, as strings.

    Empty list means the document holds nothing this module can prove
    wrong -- which is **not** the same as the objectives being good ones.
    See the module docstring for the judgement this deliberately does not
    attempt.
    """
    sections = (parse_project_goals(markdown_or_sections)
                if isinstance(markdown_or_sections, str)
                else markdown_or_sections)
    found, seen = [], {}
    for key in sorted(sections):
        section = sections[key]
        name = section.get("project", key)
        objective = section.get("objective") or {}
        if objective:
            if not objective.get("statement", "").strip():
                found.append(f"{name}: objective has no statement")
            status = objective.get("status", "").strip().lower()
            if status and status not in OBJECTIVE_STATUSES:
                found.append(
                    f"{name}: objective status {status!r} is not one of "
                    + "/".join(OBJECTIVE_STATUSES))
            # `agreed` names a second party, so it has to be able to point at
            # where the agreeing happened. Without this the word is just
            # `approved` again with a nicer spelling, set by whoever wrote the
            # file -- which is me.
            if status == "agreed" and not objective.get(
                    "conversation", "").strip():
                found.append(
                    f"{name}: objective is agreed but links no conversation "
                    "-- an agreement names where it was reached")
        results = section.get("keyResults", [])
        if len(results) > MAX_KEY_RESULTS:
            found.append(
                f"{name}: {len(results)} key results, the limit is "
                f"{MAX_KEY_RESULTS}")
        for row in results:
            label = row.get("id", "").strip() or row.get("name", "").strip()
            if not row.get("measure", "").strip():
                found.append(
                    f"{name}: key result {label!r} has no measure -- an "
                    "outcome without one is a task with a nicer name")
        for row in section.get("kpis", []):
            label = row.get("id", "").strip() or row.get("name", "").strip()
            if "target" in row:
                found.append(
                    f"{name}: KPI {label!r} carries a target -- a KPI has a "
                    "range, and a guardrail with a target gets optimised")
            if not (row.get("low", "").strip() or row.get("high", "").strip()):
                found.append(f"{name}: KPI {label!r} has no range")
        for identifier, kind in _ids(section):
            if not identifier:
                found.append(f"{name}: a {kind} has no id")
                continue
            if identifier in seen:
                found.append(
                    f"id {identifier!r} is used twice: {seen[identifier]} "
                    f"and {name}'s {kind}")
            else:
                seen[identifier] = f"{name}'s {kind}"
    return found


def key_result_ids(sections):
    """`{lowercased id: project}` over every key result. `Serves` resolves here."""
    out = {}
    for section in sections.values():
        for row in section.get("keyResults", ()):
            identifier = row.get("id", "").strip().lower()
            if identifier:
                out[identifier] = section.get("project", "")
    return out


def kpi_ids(sections):
    """`{lowercased id: project}` over every KPI -- so `Serves` can refuse one
    by name rather than calling it unknown."""
    out = {}
    for section in sections.values():
        for row in section.get("kpis", ()):
            identifier = row.get("id", "").strip().lower()
            if identifier:
                out[identifier] = section.get("project", "")
    return out


def split_serves(cell):
    """A `Serves` cell -> `[id, ...]`, lowercased, comma-separated, empty dropped.

    *"Many milestones may serve one key result; a milestone may serve two."*
    """
    return [part.strip().lower()
            for part in (cell or "").replace(";", ",").split(",")
            if part.strip()]


def serves_problems(serves, sections):
    """`{(project, milestone): serves cell}` + parsed sections -> broken pointers.

    Two findings, both of them defects a pull request can close: a `Serves`
    cell naming an id that no key result carries, and one naming a KPI --
    *"a KPI may never be used as a key result"*, issue #227's own rule.

    **The orphan is deliberately not here; it is `serves_orphans`.** It used
    to be, and folding the two together is what kept this whole check out of
    `tools.preflight`: today 43 of the seated milestones serve nothing and 32
    of those sit in the eight projects the owner scoped out until step 4, so a
    single list makes a document with no defect in it indistinguishable from
    one that is broken, forever. An orphan is also not a thing I can fix --
    it is either legitimate keep-the-lights-on work or a pruning signal for
    him -- and a finding no pull request can close is the shape
    `security_alerts` and `argocd_health` already refuse to raise on.
    """
    results, guardrails = key_result_ids(sections), kpi_ids(sections)
    found = []
    for (project, milestone) in sorted(serves):
        for identifier in split_serves(serves[(project, milestone)]):
            if identifier in guardrails:
                found.append(
                    f"{project} / {milestone}: serves {identifier!r}, which "
                    "is a KPI -- a guardrail may never be a key result")
            elif identifier not in results:
                found.append(
                    f"{project} / {milestone}: serves {identifier!r}, which "
                    "is not a key result id")
    return found


def keeps_problems(keeps, sections):
    """`{(project, milestone): Keeps cell}` + sections -> broken pointers.

    The mirror of `serves_problems`, one column to the right, and both
    halves of issue #227's rule that a guardrail and a goal are different
    kinds of thing: `Serves` naming a KPI is refused there, and `Keeps`
    naming a **key result** is refused here. Without this second half the
    rule is only enforced in one direction, and the cheapest way to make an
    orphan disappear would be to write its key result into `Keeps`.

    A cell naming an id that is neither is the same defect `serves_problems`
    reports: a pointer at nothing, which a pull request can close.
    """
    results, guardrails = key_result_ids(sections), kpi_ids(sections)
    found = []
    for (project, milestone) in sorted(keeps):
        for identifier in split_serves(keeps[(project, milestone)]):
            if identifier in results:
                found.append(
                    f"{project} / {milestone}: keeps {identifier!r}, which "
                    "is a key result -- a goal is not a guardrail")
            elif identifier not in guardrails:
                found.append(
                    f"{project} / {milestone}: keeps {identifier!r}, which "
                    "is not a KPI id")
    return found


def serves_orphans(serves, sections, keeps=None):
    """The milestones that serve no key result and keep no KPI -- rule 4.

    *"A milestone that serves nothing is one of exactly two things -- keep-
    the-lights-on work, which is legitimate and sits under a KPI rather than
    a goal, or work nobody can justify, which is the pruning signal. The
    orphan list is a deliverable of this job, not a side effect."*

    **Read that rule twice: it names two verdicts, and until the `Keeps`
    column existed this list merged them.** 43 milestones came back as one
    undifferentiated block in which `Cost and quota` -- legitimate,
    permanent, and the reason `nova-kpi-cost-per-cycle` exists at all --
    sat beside the galaxy view nobody can justify. That is the same merge
    `agentic_health` had to unpick between a failing run and a run GitHub
    refused to start: one number, two causes, opposite actions. So a seat
    that names the guardrail it keeps is *answered*, and what is left is
    the short list rule 4 is actually asking for.

    So it is an inventory rather than a defect, which is why it is a
    separate function from `serves_problems` and why its caller does not
    raise on it. `sections` is taken and unused on purpose: an orphan is
    decided by the seat's own empty cells, and reading the goals document
    to decide it would make a milestone stop being an orphan when some
    other project gained a key result. `keeps` defaults to empty so a
    caller holding only the old column gets the old answer.
    """
    guardrails = keeps or {}
    return [f"{project} / {milestone}: serves no key result and keeps no "
            "KPI -- either keep-the-lights-on work whose guardrail has not "
            "been written yet, or work nobody can justify"
            for (project, milestone) in sorted(serves)
            if not split_serves(serves[(project, milestone)])
            and not split_serves(guardrails.get((project, milestone), ""))]


#: A row in one of these is finished, so it is not work waiting for a
#: milestone. Spelled out here rather than imported from
#: `nova_boards._CLOSED_STATUS_KEYS`, which is private -- the same copy
#: `tools.board_status` keeps, for the same reason.
CLOSED_STATUS_KEYS = frozenset({"done", "outdated"})


def open_rows(rows):
    """Every row still waiting to be built, closed ones dropped.

    A `done` or `outdated` row is finished work: asking which milestone it
    serves is asking about a decision nobody will take again, and counting
    it would make the unplaced list grow forever as the boards roll.
    """
    return [row for row in (rows or [])
            if not row.get("done")
            and (row.get("statusKey") or "") not in CLOSED_STATUS_KEYS]


def _row_label(row):
    board = (row.get("board") or "row").strip() or "row"
    return f"{board} #{row.get('number')}"


def task_seat_problems(rows, serves):
    """Open rows naming a milestone that `milestone-seats.md` has no seat for.

    The last link in issue #227's chain -- objective, key result, milestone,
    **task** -- and the only one nothing checked. `serves_problems` catches a
    seat pointing at a key result that does not exist; this catches a task
    pointing at a milestone that does not exist, which is the same defect one
    level down and equally closeable by a pull request: either the row is
    pointed at the wrong milestone or the milestone needs a seat.

    Keyed on `(project, milestone)` lowercased, exactly as
    `parse_milestone_serves` keys its seats, so a row under the right
    milestone name in the wrong project is a finding rather than a silent
    pass -- the same milestone title appears under more than one project.

    A row with **no** milestone at all is deliberately not here; it is
    `task_seat_orphans`. 24 of the 125 open rows carry none today, and
    folding the two together would put this check permanently red on a
    backlog nobody can close in one pull request -- exactly the merge
    `serves_orphans` had to be split out of to get into `tools.preflight`
    at all.
    """
    found = []
    for row in sorted(open_rows(rows),
                      key=lambda r: (str(r.get("board") or ""),
                                     int(r.get("number") or 0))):
        project = (row.get("project") or "").strip()
        milestone = (row.get("milestone") or "").strip()
        if not project or not milestone:
            continue
        if (project.lower(), milestone.lower()) in (serves or {}):
            continue
        found.append(
            f"{_row_label(row)}: under {project} / {milestone!r}, which has "
            "no seat in milestone-seats.md")
    return found


def task_seat_orphans(rows):
    """Open rows under no milestone at all -- the inventory, not a defect.

    The mirror of `serves_orphans` one level down. A milestone that serves
    nothing is either lights-on work or a pruning signal; a task under no
    milestone is either work that belongs to a milestone nobody has placed
    it in, or work that should not be on the board. Both are judgements the
    owner makes on a row, not something a diff closes, so this prints and
    does not raise.

    A row with **no project** is reported here too. It cannot be seated
    either way, and reporting it as a separate third list would split one
    verdict -- "this row hangs off nothing" -- across two places.
    """
    lines = []
    for row in sorted(open_rows(rows),
                      key=lambda r: (str(r.get("board") or ""),
                                     int(r.get("number") or 0))):
        project = (row.get("project") or "").strip()
        milestone = (row.get("milestone") or "").strip()
        if milestone and project:
            continue
        if not project and milestone:
            # A seat is keyed on the pair, so this row can never match one
            # whatever milestone it names. Saying "under no milestone" here
            # would be false about a row that names one.
            why = f"no project, so its milestone {milestone!r} cannot be seated"
        elif not project:
            why = "no project, under no milestone"
        else:
            why = f"{project}, under no milestone"
        lines.append(
            f"{_row_label(row)}: {why} -- it serves no key result and no KPI "
            "by way of one")
    return lines


def _set_field_in_fence(markdown, fence, row_id, field, value):
    """Set one field inside one ```key-result fence, addressed by its `id`.

    The mirror of `nova_plan.set_field_in_goals`, and it exists for the same
    reason one level up: a key result's `now:` was typed by whichever cycle
    wrote the block, so `project-goals.md` carries numbers nobody can
    recompute. Three of the nine were copied out of `goals.md` by hand, and
    `goals.md`'s own numbers had drifted from its instrument because the
    weekly review that retypes them has never run.

    Addressed by `id` rather than by `name`, which is the one difference from
    the goals version: a key result's name is a sentence I rewrite while the
    conversation about it is still open, and its id is the string
    `milestone-seats.md`'s `Serves` column points at, so the id is the stable
    address and the name is not.

    Returns `None` — the address moved, nothing failed — when no fence carries
    that id, when two do (there is no way to tell them apart, so editing
    whichever came first would report success on the wrong one), or when the
    fence never closed. `problems()` already refuses a duplicate id, so a
    document that passes the checker cannot hit the second case; this refuses
    it anyway rather than trusting a check that runs somewhere else.
    """
    field = (field or "").strip()
    value = str("" if value is None else value).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", field) or "\n" in value:
        return None
    wanted = (row_id or "").strip()
    if not wanted:
        return None

    lines = (markdown or "").split("\n")
    id_re = re.compile(r"^(?P<indent>[ \t]*)id:[ \t]*(?P<value>.*?)[ \t]*$")
    field_re = re.compile(r"^(?P<indent>[ \t]*)" + re.escape(field) + r":[ \t]*.*$")

    hits = []
    start = None
    for index, line in enumerate(lines):
        if start is None:
            opened = _FENCE_OPEN_RE.match(line)
            if opened and opened.group("name") == fence:
                start = index
            continue
        closed = _FENCE_CLOSE_RE.match(line)
        opened = _FENCE_OPEN_RE.match(line)
        if closed or opened:
            body = range(start + 1, index)
            match = None
            for i in body:
                found = id_re.match(lines[i])
                if found and found.group("value") == wanted:
                    match = i
                    break
            if match is not None:
                if not closed:
                    return None
                hits.append((match, index, body))
            start = index if (opened and opened.group("name") == fence) else None
    if len(hits) != 1:
        return None

    match, close, body = hits[0]
    indent = id_re.match(lines[match]).group("indent")
    written = [i for i in body if field_re.match(lines[i])]
    if written:
        for i in written:
            lines[i] = f"{indent}{field}: {value}"
        for i in reversed(written[1:]):
            del lines[i]
        return "\n".join(lines)
    lines.insert(close, f"{indent}{field}: {value}")
    return "\n".join(lines)


def set_field_in_key_result(markdown, key_result_id, field, value):
    """Set one field inside the ```key-result fence carrying `key_result_id`."""
    return _set_field_in_fence(markdown, "key-result", key_result_id, field, value)


def set_field_in_kpi(markdown, kpi_id, field, value):
    """Set one field inside the ```kpi fence carrying `kpi_id`.

    The same setter as `set_field_in_key_result` above, pointed at the other
    fence, because a KPI's `now:` was typed by hand exactly the way a key
    result's was -- `nova-kpi-cost-per-cycle` carried a number copied out of a
    paragraph in `prompt.md` describing a window that closed on 08-28, until
    `tools.goal_measures` grew an instrument for it.

    What it deliberately does NOT do is touch `low:` or `high:`. Those are the
    bounds the guardrail is judged against, and rule 4 of issue #227 is that a
    KPI never carries a target -- a tool that could move the bounds to fit the
    reading is the dashboard optimising itself, which is the failure the whole
    KPI/key-result split exists to prevent. The caller passes `now`.
    """
    return _set_field_in_fence(markdown, "kpi", kpi_id, field, value)
