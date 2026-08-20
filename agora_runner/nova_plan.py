"""The `/plan` page: what Nova would do next, and what any of it is for.

Edvard, `issues.md` #7: *"Need evolve to think like a product manager.
Both so it becomes better, but also so i learn and get experience from it
since my dream is to become the worlds best platform product manager."*
Cycle 226 answered the first half by writing `roadmap.md` -- the
prioritised order and the reasoning behind it -- and Cycle 229 added
`goals.md`, the slate of proposed goals and the weekly review against
them. Both were written so he could argue with the reasoning instead of
only the result.

**Neither of them has ever reached his phone.** They live in his vault
and nowhere else, so the one document whose entire purpose is being
argued with is the one he has to open Obsidian to read. `goals.md`'s own
G2 measure names it: *"the number of things you still have to leave the
Nova app to do ... reading the roadmap. Four."* This module is that one
crossed off.

Nothing here does I/O. The two documents arrive as text and leave as a
payload, the same split every other page on this server follows --
`nova_sources.plan_markdown` fetches, this shapes.

**Why the sections are discovered rather than named.** Every other page
here parses a file with a contract: the digest has `## Next cycle`, a
board has `## Board`, the retro ledger has a validator that refuses a row
of the wrong shape. These two have no contract and should not get one.
They are Nova's own prose, restructured by whichever cycle last had
something new to say -- `roadmap.md` has changed its section list twice
already -- and a parser that named the sections it expected would render
a stale page the first time a cycle reorganised its own argument, without
saying so. So `md_sections.outline` takes whatever headings are there.
The page can be wrong about the styling of a section it has never seen;
it cannot silently drop one.
"""

import re

from agora_runner.md_sections import outline
from agora_runner.nova_journal import render_blocks

ROADMAP_PATH = "projects/sokrates/projects/nova/roadmap.md"
GOALS_PATH = "projects/sokrates/projects/nova/goals.md"

# Order is the reading order he asked for, and it is deliberately goals
# last. `roadmap.md` answers "what next", which is the question he opens
# the page with; `goals.md` answers "what for", which is the one he only
# asks when he disagrees with the first answer.
PLAN_DOCUMENTS = (
    ("roadmap", "Roadmap", ROADMAP_PATH),
    ("goals", "Goals", GOALS_PATH),
)

_UPDATED_RE = re.compile(r"^updated:[ \t]*(?P<value>.+?)[ \t]*$", re.MULTILINE)


def _updated(text):
    """The `updated:` stamp out of the frontmatter, or `""`.

    Only inside the frontmatter, and only if the file opens with one: an
    `updated:` line further down is somebody's prose, and a stamp that is
    really the middle of a sentence is worse on screen than no stamp. The
    value is not parsed into a date -- these files carry `2026-08-16` and
    the page prints it as written, so validating the format here would
    only give this module an opinion it cannot act on.
    """
    lines = (text or "").split("\n")
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            match = _UPDATED_RE.search("\n".join(lines[1:i]))
            return match.group("value") if match else ""
    return ""


def _document(key, label, text):
    """One markdown document -> one card's worth of payload.

    A missing or empty document is `missing: True` with no sections
    rather than an error. Both files are written by a cycle and could
    genuinely not exist yet -- the same call `nova_costs` and `nova_retro`
    make about their ledgers -- and a page that says "not written yet" is
    a true answer, where a 502 on the whole page would take the document
    that *is* there down with it.
    """
    text = text or ""
    if not text.strip():
        return {
            "key": key,
            "label": label,
            "title": label,
            "updated": "",
            "missing": True,
            "sections": [],
        }

    title = label
    sections = []
    for level, heading, body in outline(text):
        if level == 1 and title == label:
            # The `# ` heading is the document's own title, so it becomes
            # the card's title rather than a section inside it. Its body
            # is the standfirst the file opens with and still has to be
            # rendered, which is why this does not `continue`.
            title = heading
            level, heading = 0, None
        blocks = render_blocks(body)
        if not heading and not blocks:
            continue
        sections.append({"level": level, "heading": heading, "blocks": blocks})

    return {
        "key": key,
        "label": label,
        "title": title,
        "updated": _updated(text),
        "missing": False,
        "sections": sections,
    }


def plan_payload(documents):
    """`{key: markdown}` -> the `/plan` payload.

    Every document in `PLAN_DOCUMENTS` appears in the output whether or
    not the fetch found it, in the fixed order above. A page that renders
    only what it managed to read is a page that goes quietly from two
    cards to one, and the missing one is exactly the case worth seeing.
    """
    return {
        "documents": [
            _document(key, label, (documents or {}).get(key, ""))
            for key, label, _path in PLAN_DOCUMENTS
        ]
    }
