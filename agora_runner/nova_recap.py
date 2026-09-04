"""The twelve-hour recap card that sits at the top of the Journal page.

The owner, `issues.md` capture 2026-09-04, rated 🔴 Immediately: *"I want a
stick Journal card at the top that summarizes the last 12 hours. Keep it
short as I just want this to quickly glance over what has been done. ...
I dedininetly do not want one bullet for each cycle that has ran the last
12 hours, that is way to much. this should be max 5-6 bullets as many
cycles work on the same problem/project."*

The load-bearing sentence is the last one. A cycle is not a topic — six
cycles in a row can be one piece of work, and the digest already prints
one line per cycle, which is exactly the thing he is asking not to read
again. So the grouping is a judgement about what the work *was*, and I
did not find a way to take that judgement out of a model and get an
answer he would want. Clustering entries by PR repo, by board number or
by title keywords all produce groups that are defensible and bullets
that are not sentences.

So the recap is **written by a cycle and stored in the vault**, the same
shape as `catalog.md`: `tools.recap` prints the raw material (the entries
in the window, with their PR and Board fields), a cycle writes five or
six bullets, and this module parses the file back for the page. That
makes the card only as fresh as the last cycle that wrote it, which is
why every payload carries its own stamp and the card prints it — a
summary that silently describes a window that closed four hours ago is
worse than no card, and the reader can only tell if the card says when
it was written.

No I/O here, the same split `nova_catalog` and `nova_plan` follow:
markdown in, payload out.
"""

import datetime
import re


RECAP_PATH = "projects/sokrates/projects/agora/nova/resources/recap.md"

# How long after it was written a recap stops being a description of "the
# last 12 hours" and starts being a description of some earlier window.
# Three hours is not a measurement and I am not pretending it is one: it
# is about seven cycles at the current 24-minute heartbeat, which is long
# enough that a stale card is unusual and short enough that the note
# appears before the card is misleading. It only ever adds a line to the
# card; nothing is hidden by it.
STALE_AFTER_HOURS = 3.0

_STAMP = re.compile(
    r"<!--\s*generated:\s*(?P<when>\S+?)\s*(?:\|\s*cycles\s*(?P<cycles>[^>]*?)\s*)?-->"
)
_BULLET = re.compile(r"^-\s+(?P<text>\S.*)$")
_LEAD = re.compile(r"^\*\*(?P<lead>[^*]+?)\*\*\s*(?P<rest>.*)$")
#: `[the hub](https://hub.tailc83eb3.ts.net/)` and a bare `https://...`.
#: Both, because a cycle writing a bullet reaches for whichever is natural
#: and the reader wants a tap target either way.
_MD_LINK = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<href>[^)\s]+)\)")
_BARE_URL = re.compile(r"https?://[^\s<>\"'\]\)]+")


def _strip_frontmatter(markdown):
    if not markdown.startswith("---\n"):
        return markdown
    end = markdown.find("\n---", 3)
    if end == -1:
        return markdown
    return markdown[end + 4:]


def parse_recap(markdown, now=None):
    """`recap.md` as the page's payload.

    `now` is injected rather than read here so the freshness half is
    testable without freezing a clock — the same reason `nova_needs`
    takes `today`.
    """
    body = _strip_frontmatter(markdown or "")
    stamp = _STAMP.search(body)
    written = ""
    cycles = ""
    age_hours = None
    if stamp:
        written = stamp.group("when") or ""
        cycles = (stamp.group("cycles") or "").strip()
    bullets = []
    for line in body.splitlines():
        match = _BULLET.match(line.strip())
        if not match:
            continue
        text = match.group("text").strip()
        lead_match = _LEAD.match(text)
        if lead_match:
            lead = lead_match.group("lead").strip()
            rest = lead_match.group("rest").strip()
        else:
            lead = ""
            rest = text
        bullets.append({
            "lead": lead,
            "text": _plain(rest),
            "leadParts": link_parts(lead),
            "parts": link_parts(rest),
        })

    when = _parse_stamp(written)
    if when is not None:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        age_hours = round((now - when).total_seconds() / 3600.0, 2)

    return {
        "bullets": bullets,
        "written": written,
        "writtenLabel": _oslo_label(when),
        "cycles": cycles,
        "ageHours": age_hours,
        # Unknown age reads as stale on purpose. A card with no readable
        # stamp is the one case where the reader cannot judge for himself,
        # so it says so rather than presenting itself as current.
        "stale": age_hours is None or age_hours > STALE_AFTER_HOURS,
        "staleAfterHours": STALE_AFTER_HOURS,
    }


def link_parts(text):
    """A bullet split into runs of plain text and runs that are a link.

    His capture, 2026-09-04 12:29: *"the bullet that mentions the tailnet
    start page has been created, i immediately want to check it out but
    I'm left without a url or any clickable link so i have to search for
    it ... You can look at this board as a 'news board' or your place to
    market to me what you have created for me to check it out and give
    feedback."*

    The split happens here rather than in the page for the same reason
    the bullet count does: one definition, one test. Each part is
    `{"text": ..., "href": ...}` and `href` is `""` for the plain runs,
    so the renderer is a loop with one `if` in it and never sees markdown.

    Two spellings are understood because a cycle writing a bullet will
    reach for whichever is natural in the sentence: `[the hub](url)` and
    a bare `https://...`. Nothing else is markdown here -- bold already
    has its own meaning in this file (the lead) and inventing more syntax
    for a six-line document is not worth the parser.
    """
    parts = []
    cursor = 0
    for match in _MD_LINK.finditer(text or ""):
        _append_plain(parts, text[cursor:match.start()])
        parts.append({"text": match.group("label"), "href": match.group("href")})
        cursor = match.end()
    _append_plain(parts, (text or "")[cursor:])
    return [p for p in parts if p["text"]]


def _append_plain(parts, chunk):
    """The plain run between two markdown links, with bare URLs linked.

    A bare URL is turned into a link and keeps its own text -- he asked to
    be able to tap it, not for me to invent a label for it.
    """
    cursor = 0
    for match in _BARE_URL.finditer(chunk or ""):
        before = chunk[cursor:match.start()]
        if before:
            parts.append({"text": before, "href": ""})
        url = match.group(0)
        # A URL at the end of a sentence takes the full stop with it
        # otherwise, and the link then 404s on a character he cannot see.
        trailing = ""
        while url and url[-1] in ".,;:!?":
            trailing = url[-1] + trailing
            url = url[:-1]
        if url:
            parts.append({"text": url, "href": url})
        if trailing:
            parts.append({"text": trailing, "href": ""})
        cursor = match.end()
    tail = (chunk or "")[cursor:]
    if tail:
        parts.append({"text": tail, "href": ""})


def _plain(text):
    """The bullet as it reads with no markup, for anything that wants a string."""
    return "".join(part["text"] for part in link_parts(text))


def _parse_stamp(written):
    if not written:
        return None
    try:
        when = datetime.datetime.fromisoformat(written)
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when


def _oslo_label(when):
    """`08:41` in Oslo time, which is the only clock he reads (rule 7)."""
    if when is None:
        return ""
    try:
        oslo = when.astimezone(_oslo())
    except (ValueError, OverflowError):
        return ""
    return oslo.strftime("%H:%M")


def _oslo():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Oslo")
    except Exception:
        # The site image is alpine and a missing tzdata is a real
        # possibility there (Sokrates flagged exactly this on
        # telegram-bridge). A recap that prints UTC is a small wrong; a
        # recap page that 500s is not.
        return datetime.timezone.utc


def recap_page(payload):
    """What `/api/recap` sends. The count is computed here for the reason
    every other page in this repo computes its counts server-side: the
    number on the screen has one definition and one test."""
    page = dict(payload)
    page["total"] = len(payload.get("bullets", []))
    return page
