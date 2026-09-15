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
* a key result with no `target` -- rule 7 reads a goal as *"target versus
  current number"*, so a measure with nothing to reach is not a goal. A
  blank `now` is deliberately fine beside it: that is a reading nobody has
  an instrument for yet, where a blank target is a goal nobody agreed,
* a KPI carrying a `target:` -- *"the moment a guardrail carries a target,
  the dashboard gets optimised instead of the work"*,
* a KPI with no range at all (`low`/`high`, either bound is enough),
* an id used twice anywhere in the document, because `Serves` points at ids
  and an ambiguous pointer is worse than no pointer,
* an objective with no `statement`, or a status outside the three,
* key results or KPIs under a project with **no objective at all** --
  the issue's own title, and the inverse of rule 4's orphan milestone:
  outcomes with nothing to be outcomes of. Gated on the section holding
  goal blocks, because a section is opened for every `##` heading and a
  prose heading claims nothing,
* a key result whose `measure` or `unit` is TRL, lifecycle or satisfaction
  -- rule 8, *"none of them measures whether a goal was reached"*. Those
  three are project attributes that already carry a number, which is what
  makes them so easy to promote into a key result by accident.

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
contract: Nova writes this. One `## <Project>` section per project, holding one ```objective fence (his words, status discussing/agreed/struck, with the Agora conversation it was agreed in), 2-3 ```key-result fences (an outcome with a measure, now and target, and the same status discussing/agreed/struck) and any number of ```kpi fences (a health number with a range, never a target). A KPI is never a key result. Milestones point at a key result by id in the Serves column of milestone-seats.md. The set of projects that exist is read off the Project column on the boards, never from here.
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

OBJECTIVE_FIELDS = ("statement", "status", "conversation", "period")

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

#: **The same three words, on a key result.** His 21:02 correction deletes the
#: approval gate from *goals*, and a key result is a goal -- so `proposed` is
#: exactly as wrong here as it was on the objective above. It survived because
#: cycle 1531 changed the objective and nothing else, and the word stayed
#: legal on a key result only because nothing read the field: `problems()`
#: checked the measure, the count and the id, never the status. All fourteen
#: key results in the live document read `status: proposed` for a day, which
#: is the struck-out shape still standing in the one place nothing looked.
#:
#: Refused rather than aliased, for the reason written above: an approval is
#: not an agreement, and reading one silently as the other says they are the
#: same thing.
KEY_RESULT_STATUSES = OBJECTIVE_STATUSES

#: `period` is issue #227's seventh rule -- *"Monthly objectives, weekly
#: check. Quarterly is four hundred cycles here."* -- and until cycle 1559
#: nothing in this module could tell a month-old objective from one written
#: this morning, because an objective carried no date at all. It is one
#: month, `YYYY-MM`, and it is the month the objective covers rather than
#: the day it was written: two objectives written a week apart can cover the
#: same month, and the question rule 7 asks is only ever whether the month
#: is over.
_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def month_name(period):
    """`2026-09` -> `September 2026`; anything else back verbatim.

    Not `strftime`: that reads the process locale, so the same document
    would render differently on two boxes, and a month name is something
    the owner reads on his phone.
    """
    if not _PERIOD_RE.match(period or ""):
        return period
    year, month = period.split("-")
    return f"{_MONTHS[int(month) - 1]} {year}"


#: *"Two or three key results per project. Fifteen is a backlog wearing a
#: hat."* The floor is not checked: a project mid-proposal legitimately has
#: one, and refusing that would block the step that writes the second.
MAX_KEY_RESULTS = 3

#: Issue #227's eighth rule: *"TRL, lifecycle and satisfaction stay as
#: attributes, not scores. TRL says how mature the artefact is and a
#: hardened project can still be pointless; lifecycle decides whether a
#: project earns capacity at all; satisfaction is his own reading. None of
#: them measures whether a goal was reached."*
#:
#: All three already exist, as frontmatter on a project row, and all three
#: carry a number or a stage -- which is exactly what makes them look like
#: ready-made key results. *"Raise Nova from TRL 6 to TRL 8"* has a measure,
#: a `now` and a `target`, so every other rule in `problems()` passes it.
#: This is the one that does not.
#:
#: It is checked on key results only, and not on KPIs, because rule 8's own
#: sentence is about what measures *whether a goal was reached* -- a key
#: result is that thing. A guardrail watching an attribute stay in bounds is
#: a different claim and the issue does not forbid it.
ATTRIBUTE_MEASURES = (
    ("trl", r"\btrls?\b|\btechnology readiness\b"),
    ("lifecycle", r"\blifecycles?\b"),
    ("satisfaction", r"\bsatisfaction\b"),
)

_ATTRIBUTE_RES = tuple(
    (word, re.compile(pattern, re.I)) for word, pattern in ATTRIBUTE_MEASURES)

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
            period = objective.get("period", "").strip()
            if period and not _PERIOD_RE.match(period):
                found.append(
                    f"{name}: objective period {period!r} is not a month "
                    "-- rule 7 wants YYYY-MM, and a date this cannot read "
                    "is an objective nothing can age")
        results = section.get("keyResults", [])
        # The issue's own title -- *"give every project a goal"* -- and the
        # one shape of it nothing here reported. A section carrying key
        # results or KPIs and no ```objective fence is the inverse of rule
        # 4's orphan milestone: work pointed at outcomes that are pointed at
        # nothing. Every other rule passes it, because every other rule
        # reads a fence that is there.
        #
        # It is gated on the section having goal blocks rather than raised
        # on a bare `##` heading, and that is deliberate: `parse_project_goals`
        # opens a section for *every* heading in the document, so a prose
        # heading he types between projects would otherwise read as a defect.
        # A heading with nothing under it claims nothing; a heading with key
        # results under it claims they serve an objective.
        if not objective and (results or section.get("kpis")):
            found.append(
                f"{name}: has key results or KPIs and no objective -- they "
                "are outcomes with nothing to be outcomes of")
        if len(results) > MAX_KEY_RESULTS:
            found.append(
                f"{name}: {len(results)} key results, the limit is "
                f"{MAX_KEY_RESULTS}")
        for row in results:
            label = row.get("id", "").strip() or row.get("name", "").strip()
            measure = row.get("measure", "").strip()
            if not measure:
                found.append(
                    f"{name}: key result {label!r} has no measure -- an "
                    "outcome without one is a task with a nicer name")
            # Rule 7: *"Target versus current number says everything."* The
            # measure above says what is counted; the target says which
            # number means the goal was reached, and without it there is a
            # number going up and nothing it is going up towards.
            #
            # `now` is deliberately NOT checked beside it, and the asymmetry
            # is the point. A blank `now` is honest -- four key results carry
            # one today because no instrument exists to take the reading yet,
            # and `/plan` prints "Not measured yet." for exactly that. A
            # blank `target` is never a missing measurement: it is a number
            # the two of us settle on in the conversation, so its absence
            # means the goal was never agreed rather than never read.
            if not row.get("target", "").strip():
                found.append(
                    f"{name}: key result {label!r} has no target -- rule 7 "
                    "reads a goal as target against current number, and a "
                    "measure with nothing to reach is not one")
            # Named apart from the objective's `status` above so the two
            # are never confused while reading this function.
            result_status = row.get("status", "").strip().lower()
            if result_status and result_status not in KEY_RESULT_STATUSES:
                found.append(
                    f"{name}: key result {label!r} has status "
                    f"{result_status!r}, "
                    "which is not one of "
                    + "/".join(KEY_RESULT_STATUSES)
                    + " -- goals are agreed in a conversation, not approved")
            # Rule 8. Read the `measure` and the `unit`, which are the two
            # fields that say what is being counted; `name` is deliberately
            # left out, because a key result may legitimately be *about* the
            # work that earns a lifecycle stage without the stage being the
            # number.
            for word, pattern in _ATTRIBUTE_RES:
                if pattern.search(measure) or pattern.search(
                        row.get("unit", "")):
                    found.append(
                        f"{name}: key result {label!r} measures {word} -- "
                        "rule 8 keeps TRL, lifecycle and satisfaction as "
                        "project attributes, and none of them says whether "
                        "a goal was reached")
                    break
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


def _number(value):
    """`"6"` -> `6.0`; anything that is not one number -> `None`.

    A `now` of `not measured`, a blank, or `1.6M` is not a reading this can
    compare, and the honest answer to "is it in bounds" for all three is that
    nobody knows. `kpi_breach` returns `None` for those rather than guessing,
    which is the same call `_seat_sentence` makes on a seats file it could not
    read: unmeasured and in-bounds are not the same state.
    """
    try:
        return float((value or "").strip())
    except (TypeError, ValueError):
        return None


def kpi_breach(row):
    """One KPI fence -> the sentence saying it is out of its range, or `None`.

    **This is the only thing in the model that reads a KPI's own numbers
    against each other.** Issue #227 defines a KPI as *"health numbers with a
    range rather than a target, for the things that must stay in bounds while
    the work happens"* -- so a range that nothing ever compares the current
    value to is decoration. `problems()` refuses a KPI with no range and a KPI
    carrying a target; neither of those notices a KPI sitting outside the
    range it does have. `/plan` printed both numbers in adjacent sentences --
    *"Now 6."* and *"In bounds 0 to 1."* -- and left the comparison to whoever
    was reading, which on a phone is nobody.

    Measured on the live document the day this was written: ten KPIs, and
    `nova-kpi-silent-cycles` reads 6 against a ceiling of 1. That guardrail
    had been breached six times over and no tool, page or check said a word.

    Returns the phrase rather than a boolean so the check and the page say the
    same sentence -- `"6 is above the ceiling of 1"` -- and a reader never has
    to hold two wordings for one finding.
    """
    now = _number(row.get("now"))
    if now is None:
        return None
    unit = row.get("unit", "").strip()
    unit = f" {unit}" if unit else ""
    high = _number(row.get("high"))
    if high is not None and now > high:
        return (f"{row.get('now', '').strip()}{unit} is above the ceiling "
                f"of {row.get('high', '').strip()}{unit}")
    low = _number(row.get("low"))
    if low is not None and now < low:
        return (f"{row.get('now', '').strip()}{unit} is below the floor "
                f"of {row.get('low', '').strip()}{unit}")
    return None


def kpi_breaches(sections):
    """Every KPI outside its own range, as report lines.

    An inventory rather than a model defect, the same call `split_orphans`
    makes: the document is well formed and the *system* is out of bounds, so
    this is a reading to act on rather than a file to fix. It is what a
    guardrail is for, and until it existed the range was written down and
    never read.
    """
    out = []
    for section in sections.values():
        name = section.get("project", "")
        for row in section.get("kpis", ()):
            breach = kpi_breach(row)
            if breach:
                label = row.get("id", "").strip() or row.get(
                    "name", "").strip()
                out.append(f"{name} / {label}: {breach}")
    return out


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


def _same_project(seat_project, goal_project):
    """Does a seat's project name and a goal's project name mean one project?

    `parse_milestone_serves` keys its rows by the project name as the board
    writes it and `key_result_ids` carries the name as the goals document
    writes it -- `nova` against `Nova`, `product management` against
    `Product management` -- so the two are compared case-folded and
    stripped rather than raw.
    """
    return (seat_project or "").strip().lower() == (
        goal_project or "").strip().lower()


def split_serves(cell):
    """A `Serves` cell -> `[id, ...]`, lowercased, comma-separated, empty dropped.

    *"Many milestones may serve one key result; a milestone may serve two."*
    """
    return [part.strip().lower()
            for part in (cell or "").replace(";", ",").split(",")
            if part.strip()]


def serves_problems(serves, sections):
    """`{(project, milestone): serves cell}` + parsed sections -> broken pointers.

    Three findings, all of them defects a pull request can close: a `Serves`
    cell naming an id that no key result carries, one naming a KPI --
    *"a KPI may never be used as a key result"*, issue #227's own rule --
    and one naming a key result that belongs to a **different project**.

    That third one is the quiet one, and it is why it is here rather than
    left to reading. Issue #227's model is a tree -- project, then its
    objective, then that objective's key results, then the milestones that
    serve them -- so a milestone under one project pointing at another
    project's key result is not a milestone with an unusual pointer, it is
    a branch grafted onto the wrong trunk. Nothing downstream notices:
    the id resolves, so the seat reads as filled, `split_orphans` drops
    the row off rule 4's pruning list, and `/plan` counts the milestone in
    the other project's "Served by N milestones." line. Measured on the
    live documents the day this landed: 58 seats, 0 cross-project pointers,
    so the rule starts green -- which is the point of adding it now, with
    19 more seats still to be written for the projects that have no goals
    yet, rather than after a copy-paste has put one in.

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
            elif not _same_project(project, results[identifier]):
                found.append(
                    f"{project} / {milestone}: serves {identifier!r}, which "
                    f"belongs to {results[identifier]!r} -- a milestone "
                    "serves a key result of its own project")
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
    reports: a pointer at nothing, which a pull request can close. So is a
    cell naming a KPI that belongs to another project -- `Keeps` sits in
    the same tree as `Serves` and a guardrail held by someone else's
    project is not this milestone's guardrail.
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
            elif not _same_project(project, guardrails[identifier]):
                found.append(
                    f"{project} / {milestone}: keeps {identifier!r}, which "
                    f"belongs to {guardrails[identifier]!r} -- a milestone "
                    "keeps a KPI of its own project")
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
    raise on it. Membership is decided by the seat's own empty cells and
    by nothing else -- reading the goals document to decide *membership*
    would make a milestone stop being an orphan when some other project
    gained a key result. `sections` is used only to say *why* each line
    is here, by `split_orphans`, which never drops one. `keeps` defaults
    to empty so a caller holding only the old column gets the old answer.
    """
    prunable, finished, awaiting = split_orphans(serves, sections, keeps)
    return sorted(prunable + finished + awaiting)


def project_has_goals_to_serve(project, sections):
    """Is there anything under `project` for a milestone to point at?

    A project section with no key result and no KPI offers nothing, so a
    milestone under it cannot name one. Keyed lowercase, the way
    `parse_project_goals` keys its output.
    """
    section = (sections or {}).get((project or "").strip().lower())
    if not section:
        return False
    return bool(section.get("keyResults") or section.get("kpis"))


def split_orphans(serves, sections, keeps=None, rows=None):
    """The orphan list, split by *why* the seat is empty.

    Returns `(prunable, finished, awaiting)`.

    **Same call `serves_orphans` already made once, one level up.** That
    docstring splits an orphan from a seat that names its guardrail,
    citing `agentic_health`: one number, two causes, opposite actions. This
    is the other cause hiding in the number. Measured Cycle 1555 against
    the live documents: 36 orphans, of which **32 sit under a project that
    has no objective, no key result and no KPI written at all**. There is
    nothing for those seats to serve -- writing that project's goals is
    the action, and it is blocked on the prune thread -- while the 4 under
    a project that *does* have goals are the pruning signal rule 4 is
    actually asking for. Printed as one block of 36 under one sentence
    offering two verdicts, the 4 are unreadable.

    **Nothing leaves the orphan list.** `serves_orphans` still returns all
    36 and the count in the summary is unchanged; this only says which of
    the rule's two verdicts each line is under. That is deliberate, and it
    is the answer to the worry `serves_orphans` writes down -- reading the
    goals document must not make a milestone *stop* being an orphan. It
    also reads only the seat's **own** project, so another project gaining
    a key result cannot move this line; the only thing that moves it is
    goals being written for the project the milestone is under, which is
    exactly when it becomes a question worth asking.

    **`finished` is the same split again, one cause further down.** Rule 4
    offers two verdicts and both of them assume there is work under the
    milestone to keep or to drop. Measured Cycle 1568 against the live
    boards: of the 6 milestones on the pruning list, `agora / operator
    visibility` carries five rows and **every one of them is closed**, and
    `marcus / body and progress tracking` carries three, all done. Those are
    not prioritisation questions -- there is nothing under them to justify --
    and printed under the same sentence as `nova / seeing the loop work`,
    which carries three open rows including two 🟠 High, they read as four
    equal decisions when two of them are free.

    `rows` is the boards, and it is optional for the same reason `keeps`
    is: a caller holding only the seats file gets the old two-way answer
    with an empty `finished`. Membership still comes from the seat's own
    empty cells; the boards only say which of rule 4's verdicts has already
    been answered by the work itself.
    """
    guardrails = keeps or {}
    open_counts = _open_rows_by_milestone(rows)
    prunable, finished, awaiting = [], [], []
    for (project, milestone) in sorted(serves):
        if split_serves(serves[(project, milestone)]):
            continue
        if split_serves(guardrails.get((project, milestone), "")):
            continue
        if not project_has_goals_to_serve(project, sections):
            awaiting.append(
                f"{project} / {milestone}: serves no key result and keeps "
                "no KPI -- and there is none to serve, because no key "
                "result or KPI is written for this project yet")
            continue
        key = ((project or "").strip().lower(), (milestone or "").strip().lower())
        if rows is not None and not open_counts.get(key):
            finished.append(
                f"{project} / {milestone}: serves no key result and keeps "
                "no KPI -- and no row under it is still open, so there is "
                "nothing here to keep: retire the milestone")
        else:
            prunable.append(
                f"{project} / {milestone}: serves no key result and keeps "
                "no KPI -- either keep-the-lights-on work whose guardrail "
                "has not been written yet, or work nobody can justify")
    return prunable, finished, awaiting


def _open_rows_by_milestone(rows):
    """`{(project, milestone) lowercased: open row count}`.

    Keyed off the row's own two fields rather than the seats file, because
    the question is what is still on the boards under that name. A row with
    no milestone cannot answer it and is dropped; `task_seat_orphans` is
    what reports those.
    """
    counts = {}
    for row in open_rows(rows):
        milestone = (row.get("milestone") or "").strip().lower()
        if not milestone:
            continue
        key = ((row.get("project") or "").strip().lower(), milestone)
        counts[key] = counts.get(key, 0) + 1
    return counts


def objective_periods(sections, today):
    """Rule 7's weekly check -> `(past, undated)`, two lists of strings.

    Issue #227's seventh rule is *"Monthly objectives, weekly check"*, and
    it was the one rule of the eight with no mechanism behind it at all:
    an objective carried a statement, a status and a conversation, so the
    document could not tell September's objective from one written in June
    and nothing anywhere asked. A goal that quietly rolls forever is the
    failure the rule names.

    `today` is a `datetime.date` and is **required**, not defaulted here.
    A month comparison against an implicit clock is the class of test that
    passes against broken code because CI runs in UTC and the fixture was
    written in Oslo; the caller owns the clock and the tests pin it.

    **A struck objective is never past its period.** It is a decision kept
    so it can be read back -- the same reason a struck goal keeps its block
    on `/plan` -- and asking the owner to re-cut a goal he has already killed
    is the check inventing work. `discussing` is included on purpose: an
    objective whose month ended while it was still being argued about is
    exactly the thing rule 7 is watching for.

    Neither list is a defect and neither raises. A month that has ended is
    a conversation to have with him, not something a pull request closes --
    the same call `serves_orphans` makes on the pruning list.
    """
    now = f"{today.year:04d}-{today.month:02d}"
    past, undated = [], []
    for key in sorted(sections):
        section = sections[key]
        objective = section.get("objective") or {}
        if not objective:
            continue
        name = section.get("project", key)
        if objective.get("status", "").strip().lower() == "struck":
            continue
        period = objective.get("period", "").strip()
        if not period:
            undated.append(
                f"{name}: objective names no period -- nothing can tell "
                "whether this month's goal is this month's")
            continue
        if _PERIOD_RE.match(period) and period < now:
            past.append(
                f"{name}: objective covers {month_name(period)}, which "
                f"ended before {month_name(now)} -- re-cut it with him or "
                "carry it forward on purpose")
    return past, undated


def undecided_goals(sections):
    """What is still being argued about -> a list of one line per project.

    Issue #227's sixth rule as he re-cut it himself, 2026-09-14 21:02:
    *"Not where you guess something and i just approve or decline."* So an
    objective is either still being argued about (`discussing`) or settled
    between us (`agreed`), and a goal nobody has settled is the state this
    job is actually in until it is not.

    Nothing here reported that. `report` printed `MODEL HOLDS` and
    `12 of 12 project(s) have a goal` while every one of the twelve
    objectives and all thirty-two key results read `discussing` -- which is
    the true sentence *a goal exists* standing exactly where the false one
    *the goals are settled* would go. Six of my cycles each re-derived
    "still waiting on him" by reading a 60KB document, and then wrote it
    into a digest line instead of into the instrument. That is the same
    shape as `security_alerts` re-proving one patched advisory three cycles
    running: a fact that lives in prose gets re-measured forever.

    `struck` is decided and never listed -- it is a goal he killed, and
    asking again is the check inventing work, the same call
    `objective_periods` makes. A missing status reads as `discussing`,
    because `DEFAULT_OBJECTIVE_STATUS` is what the document means by
    silence.

    Not a defect and it does not raise. Agreeing a goal is a conversation
    with him, not something a pull request closes.
    """
    lines = []
    for key in sorted(sections):
        section = sections[key]
        name = section.get("project", key)
        objective = section.get("objective") or {}
        if not objective:
            continue
        undecided_objective = _is_undecided(objective)
        pending = [
            (row.get("id") or row.get("name") or "?").strip()
            for row in section.get("keyResults", ())
            if _is_undecided(row)
        ]
        if not undecided_objective and not pending:
            continue
        parts = []
        if undecided_objective:
            # `discussing` is a claim that a conversation is happening, and
            # for eleven of the twelve objectives there was none: the word
            # was set by me, in the document, with nothing on the other end
            # of it. `problems` already refuses the mirror of this --
            # `agreed` with no conversation, because an agreement names
            # where it was reached -- and the same sentence is true one
            # status earlier. Reported here rather than raised: opening the
            # thread is a thing to do, not a defect in the document.
            if (objective.get("conversation") or "").strip():
                parts.append("the objective")
            else:
                parts.append(
                    "the objective, which names no conversation -- nothing "
                    "is arguing it")
        if pending:
            parts.append(
                f"{len(pending)} key result(s): " + ", ".join(pending))
        lines.append(f"{name}: still discussing " + "; ".join(parts))
    return lines


def _is_undecided(block):
    """`discussing`, written or meant by silence. `agreed`/`struck` are not."""
    status = (block.get("status") or "").strip().lower()
    return (status or DEFAULT_OBJECTIVE_STATUS) == "discussing"


def unpointed_goals(serves, keeps, sections):
    """The goals side of rule 4, read backwards: what nothing points at.

    `serves_orphans` starts at a seat and asks what it names. This starts at
    a key result or a KPI and asks whether any seat names *it*. Both
    directions are needed and neither implies the other: the whole seats
    file can resolve perfectly while a key result has no work under it at
    all, which is an outcome nobody is pursuing, and a KPI with no keeper is
    a number on the scoreboard that no milestone is accountable for holding
    in bounds.

    **Each id is looked for in its own column only.** A key result named in
    a `Keeps` cell does not count as served, and a KPI named in `Serves`
    does not count as kept -- both of those are already defects
    `keeps_problems` and `serves_problems` raise on, and honouring them here
    would let a broken pointer silence this inventory. That is the same
    separation the `Keeps` column was added for.

    An inventory rather than a defect, like the orphan list: which milestone
    ought to carry a given number is a judgement, not something a diff
    closes.
    """
    served, kept = set(), set()
    for cell in serves.values():
        served.update(split_serves(cell))
    for cell in (keeps or {}).values():
        kept.update(split_serves(cell))
    found = []
    for identifier, project in sorted(key_result_ids(sections).items()):
        if identifier not in served:
            found.append(f"{project or '?'} / {identifier}: a key result no "
                         "milestone serves -- nothing on the seats file is "
                         "being built toward it")
    for identifier, project in sorted(kpi_ids(sections).items()):
        if identifier not in kept:
            found.append(f"{project or '?'} / {identifier}: a KPI no "
                         "milestone keeps -- no milestone is accountable for "
                         "holding it in bounds")
    return found


def projects_without_goals(rows, sections):
    """Issue #227's own title, counted -> `(lines, projects on the boards)`.

    *"Give every project a goal"* is the sentence this whole issue is
    named after, and nothing anywhere said how far along it is. The
    check reported 19 orphan milestones "awaiting project goals", which
    is the same fact read through the seats file, one milestone at a
    time -- so the number of *projects* still without an objective was
    derivable and never stated, and a project carrying board rows but no
    milestone seat at all would not appear in it in any form.

    **The set of projects is read off the boards, never off this
    document.** That is `project-goals.md`'s own frontmatter contract:
    *"The set of projects that exist is read off the Project column on
    the boards, never from here."* Reading it from the goals file would
    make the check congratulate itself -- every project it knows about
    would have a goal by construction, which is the guaranteed-positive
    trap.

    **A project counts as having a goal when its section carries an
    ```objective fence**, the same gate `problems()` uses when it refuses
    key results with nothing to be outcomes of. A fence whose statement is
    blank is already a defect there and is not re-reported here as an
    absence; the two are different claims.

    An inventory rather than a defect, like the orphan list and the KPI
    breaches: a project with no goal is either work waiting for a
    conversation with him or a project that should stop existing, and
    neither is something a pull request closes.
    """
    missing, total = project_goal_coverage(rows, sections)
    return [f"{entry['project']}: no objective is written for this project "
            f"-- {entry['openRows']} open row(s) on the boards and nothing "
            "saying what any of them is for"
            for entry in missing], total


def project_goal_coverage(rows, sections):
    """The same reading as `projects_without_goals`, before it is a sentence.

    `([{project, openRows}], projects on the boards)`, sorted by the name
    the boards spell. The two callers want different things out of one
    count: the check prints a paragraph per project, and the `/plan` page
    prints `5 of 12 projects have no goal` and the names. Formatting in
    here and re-parsing it there is how one reading becomes two that can
    disagree, so the numbers live here and the wording lives at each end.
    """
    with_goals = {key for key, section in (sections or {}).items()
                  if section.get("objective")}
    seen, open_counts = {}, {}
    for row in rows or ():
        name = (row.get("project") or "").strip()
        if name:
            seen.setdefault(name.lower(), name)
    for row in open_rows(rows or ()):
        name = (row.get("project") or "").strip()
        if name:
            open_counts[name.lower()] = open_counts.get(name.lower(), 0) + 1
    missing = [{"project": seen[key], "openRows": open_counts.get(key, 0)}
               for key in sorted(seen) if key not in with_goals]
    return missing, len(seen)


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
