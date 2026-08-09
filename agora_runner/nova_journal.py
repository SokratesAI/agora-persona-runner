"""Parsers turning Nova's two vault markdown files into render-ready data.

Kept apart from nova_site.py (which serves them) because this half is
pure: text in, dicts out, no network. That is deliberate -- the journal
has accumulated four mutually incompatible heading formats over 49
cycles and every one of them is still in the file, so this is the part
that actually needs tests.

The four, oldest to newest, all of which appear in the live file today:

    ### 2026-08-02 — Cycle 5
    ### 2026-08-03 03:19Z — Cycle 6, closing status (two lines, ...)
    ### Cycle 29 — 2026-08-05, 14:10 Oslo — I stopped at 8% and banked ...
    ### 2026-08-09 04:20 (Oslo) — Cycle 49

So nothing here pattern-matches a whole heading. Each em-dash-separated
segment is classified independently as date/time metadata, a cycle
number, or prose -- which is why a heading carrying no cycle number at
all (`### 2026-08-02 — Edvard's first message (not a cycle)`) still
parses into a renderable entry instead of being dropped.
"""

import math
import re

JOURNAL_PATH = "projects/sokrates/projects/agora/nova/journal.md"
DIGEST_PATH = "projects/sokrates/projects/agora/journal-digest.md"

_ENTRY_HEADING_RE = re.compile(r"^###[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# No trailing \b: `03:19Z` is one of the real heading formats, and a word
# boundary between "19" and "Z" does not exist.
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}")
_CYCLE_RE = re.compile(r"\bCycle[ \t]+(\d+)", re.IGNORECASE)
_TZ_TOKEN_RE = re.compile(r"\boslo\b|\bZ\b", re.IGNORECASE)
_SEGMENT_SPLIT_RE = re.compile(r"[ \t]+—[ \t]+")
# The one rigid part of an entry (personality.md): a `---` rule, then the
# PR and outcome on one line.
#
# Two things keep this off a `---` used as an ordinary rule mid-entry, and
# it is worth being precise about which does what, because a mutation run
# (2026-08-09) showed one of them carrying all the weight. The literal
# `\n---\nPR:` is what rejects a rule not followed by a PR line -- adding
# re.DOTALL here changes nothing at all. The `$` is what stops `outcome`
# from matching a single character, since `.+?` is non-greedy: without it
# `Outcome: merged` parses as `m`.
_FOOTER_RE = re.compile(
    r"\n-{3,}[ \t]*\nPR:[ \t]*(?P<pr>.+?)[ \t]*\|[ \t]*Outcome:[ \t]*(?P<outcome>.+?)[ \t]*$",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_DIGEST_LINE_RE = re.compile(
    r"^\*\*Cycle[ \t]+(?P<cycle>\d+)\*\*[ \t]*\((?P<at>[^)]*)\)[ \t]*—[ \t]*(?P<text>.*)$",
    re.DOTALL,
)
# What "Needs Edvard" says when it has nothing in it. Item 3 of idea #34
# wants that section completely invisible rather than showing the word
# "Nothing", so the emptiness test lives here next to the parsing.
#
# Emphasis is stripped before the comparison, and that is the whole of the
# bug Edvard reported on 2026-08-09 -- "the 'needs Edvard' box should not
# show when nothing is expected". The section was compared literally, so
# `Nothing.` counted as empty and `**Nothing.**` counted as a live claim
# on his attention. Every cycle writes the bold one, because bold is the
# house style for that section, so the box had never once been correctly
# hidden since it shipped.
_EMPTY_NEEDS = ("", "nothing", "none")
_EMPHASIS_RE = re.compile(r"[*_`]")


def is_empty_needs(text):
    """True if the `Needs Edvard` section is asking for nothing."""
    plain = _EMPHASIS_RE.sub("", text or "").strip().lower()
    return plain.rstrip(".").strip() in _EMPTY_NEEDS


def _is_metadata_only(segment):
    """True if a heading segment carries only a date/time and punctuation."""
    rest = _DATE_RE.sub("", segment)
    rest = _TIME_RE.sub("", rest)
    rest = _TZ_TOKEN_RE.sub("", rest)
    return not re.search(r"[A-Za-z0-9]", rest)


def parse_heading(heading):
    """Split one `### ...` line into date, time, cycle number and title.

    Any of the four may be absent; a heading with no cycle number is a
    real entry (Edvard's own message, Cycle 6's addendum) and gets
    `cycle: None` rather than being skipped.
    """
    date_match = _DATE_RE.search(heading)
    time_match = _TIME_RE.search(heading)
    cycle_match = _CYCLE_RE.search(heading)

    prose = []
    for segment in _SEGMENT_SPLIT_RE.split(heading):
        segment = segment.strip()
        if not segment or _is_metadata_only(segment):
            continue
        segment = _CYCLE_RE.sub("", segment, count=1).strip()
        # Leftover separators once the cycle number is lifted out of a
        # segment it shared with prose: `Cycle 30, postscript`.
        segment = segment.strip(" \t,–—-")
        # Cycles 1-19 suffixed every heading with the persona name back
        # when there was more than one persona. It is not a title.
        if not segment or segment.lower() == "(nova)":
            continue
        prose.append(segment)

    return {
        "cycle": int(cycle_match.group(1)) if cycle_match else None,
        "date": date_match.group(0) if date_match else "",
        "time": time_match.group(0) if time_match else "",
        "title": " — ".join(prose),
    }


_OUTCOME_SPLIT_RE = re.compile(r"^(?P<label>[A-Za-z-]+(?:[ \t]+[A-Za-z-]+)?)[ \t]*(?P<detail>[(,—-].*)?$", re.DOTALL)


def split_outcome(outcome):
    """`Outcome:` -> the word for the badge, and the rest as detail.

    Five of the 51 live entries qualify their outcome in prose --
    `stuck — CI outage, merged nothing`, `shipped (vault-only: issues #34
    boarded and done, ...)`. All of it is true and none of it fits in an
    uppercase pill, so the qualifier is separated out rather than
    truncated away: the badge shows the word, the detail sits beside it.
    Nothing is dropped, which is the whole point.
    """
    outcome = (outcome or "").strip()
    if not outcome:
        return "", ""
    match = _OUTCOME_SPLIT_RE.match(outcome)
    if not match:
        return outcome, ""
    label = match.group("label").strip()
    detail = (match.group("detail") or "").strip().lstrip("(,—- ").rstrip(")").strip()
    return label, detail


# Edvard, ideas.md 2026-08-09: "I actually see that The hashtag to the prs
# are listed, but they are not clickable. Maybe leave it as is, but make
# the listed prs be clickable links."
#
# "Leave it as is" is the constraint that shapes this. The `PR:` footer is
# free text and the live file holds 47 distinct shapes of it -- `#28`,
# `agora#45`, `runner#58, runner-config#6, platform-config#490`,
# `#38 (runner) + bridge#11`, `#40 (SokratesAI/agora)`, `#49, #50 (both
# merged)`. So this linkifies the references it recognises and leaves
# every other character exactly where it stood; it never reformats,
# reorders or drops any of the field. A reference it cannot place stays
# plain text rather than becoming a confidently wrong link.
_ORG = "SokratesAI"
# A bare `#12` is the runner: it is this repo, and it is what every entry
# written before the loop touched a second repo meant.
_DEFAULT_REPO = "agora-persona-runner"
_REPO_ALIASES = {
    "runner": "agora-persona-runner",
    "bridge": "agora-claude-bridge",
    "agora": "agora",
    # Bare `config` appears once, in Cycle 6's `#32, #31, config#2`, and
    # the runner's config repo is what it meant.
    "config": "agora-persona-runner-config",
    "runner-config": "agora-persona-runner-config",
    "bridge-config": "agora-claude-bridge-config",
    "platform-config": "platform-config",
    "agora-config": "agora-config",
    "agora-persona-runner": "agora-persona-runner",
    "agora-persona-runner-config": "agora-persona-runner-config",
    "agora-claude-bridge": "agora-claude-bridge",
    "agora-claude-bridge-config": "agora-claude-bridge-config",
}
_PR_REF_RE = re.compile(r"(?P<repo>[A-Za-z][A-Za-z0-9-]*)?#(?P<num>\d+)")
# A parenthetical naming the repo, which three entries use instead of a
# prefix: `#38 (runner)`, `#40 (SokratesAI/agora)`. It is only ever
# consumed when it resolves to a real repo, so `#51 (merged)` and
# `#49 (open)` keep their qualifier as text and fall back to the default.
_PR_QUALIFIER_RE = re.compile(r"^[ \t]*\((?P<name>[A-Za-z][A-Za-z0-9._/-]*)\)")


def _repo_url(name):
    """An alias or an `Owner/Repo` -> the GitHub path, or None if unknown."""
    if not name:
        return None
    if "/" in name:
        return name
    alias = _REPO_ALIASES.get(name.lower())
    return f"{_ORG}/{alias}" if alias else None


def parse_pr_refs(pr):
    """The `PR:` field -> spans, every `repo#123` in it carrying a url.

    Same span shape as `render_inline`, for the same reason: app.js builds
    every node with textContent, so a link has to arrive as structured data
    with its href already separated out. There is no path by which the
    vault's text can become markup.
    """
    text = pr or ""
    spans = []
    cursor = 0
    for match in _PR_REF_RE.finditer(text):
        if match.start() < cursor:  # already inside a consumed qualifier
            continue
        prefix = match.group("repo")
        repo = _repo_url(prefix)
        if prefix and repo is None:
            continue  # an unrecognised prefix: leave the whole thing as text
        end = match.end()
        if repo is None:
            qualifier = _PR_QUALIFIER_RE.match(text[end:])
            retarget = _repo_url(qualifier.group("name")) if qualifier else None
            if retarget:
                repo, end = retarget, end + qualifier.end()
            else:
                repo = f"{_ORG}/{_DEFAULT_REPO}"
        if match.start() > cursor:
            spans.append({"kind": "text", "text": text[cursor:match.start()]})
        spans.append(
            {
                "kind": "link",
                "text": text[match.start():end],
                "url": f"https://github.com/{repo}/pull/{match.group('num')}",
            }
        )
        cursor = end
    if cursor < len(text):
        spans.append({"kind": "text", "text": text[cursor:]})
    return spans


# Edvard, issues.md 2026-08-09: "Would be fun to use some emojis to
# represent what was done that cycle."
#
# Derived from the text rather than written into each entry's footer,
# because the ask covers the 57 entries that already exist and none of
# them can be rewritten -- `## Entries` is append-only, which is rule 3.
# A footer field would only ever emoji the future.
#
# This is a scanning aid, not a classifier. There is no ground truth for
# "what cycle 43 was about", so it was tuned by reading its output over
# all 57 live entries until nothing was misleading -- which is a weaker
# claim than correct, and worth saying out loud rather than implying.
#
# The siren is deliberately not in the table below. An outage is a
# severity, not a topic: a cycle that spent two hours down also talks
# about pods and manifests, and on plain scoring the infrastructure
# vocabulary wins -- Cycles 53 and 54 both came out as routine wrench
# work, which is a materially misleading thing to show someone scanning
# for what went wrong. So it overrides instead.
#
# It matches the opening paragraph only, and deliberately omits the
# obvious words "incident", "down for" and "killed mid". This corpus is
# relentlessly self-referential -- every entry narrates the previous
# cycle's failures -- so "mentions an outage" and "was an outage" share
# a vocabulary entirely. Over whole bodies, 17 of 57 entries fire; over
# the opening, where a cycle says what *it* did, 5 do.
_INCIDENT_RE = re.compile(
    r"outage|oomkill|crashloop|admission rejected|broke the bridge"
    r"|no cycle ran|was already dying"
)
_INCIDENT_EMOJI = "🚨"
_TOPIC_EMOJI = (
    ("🔒", r"\brbac\b|clusterrole|networkpolicy|forbidden|credential|secret|tailnet|gitleaks"),
    ("🌐", r"\bsite\b|\bpwa\b|website|frontend|browser|capture box|app\.js|service worker"),
    ("💓", r"heartbeat|cron|schedule|cadence|poll loop"),
    ("⚙️", r"kubernetes|kubectl|manifest|argocd|deploy|namespace|limitrange|\bpod\b"),
    ("🧠", r"identity\.md|personality\.md|prompt\.md|playbook|constitution|how to work"),
    ("📊", r"quota|token cost|cost table|memory\.peak|measured|metrics|percent"),
    ("📓", r"journal|digest|vault|obsidian|issues\.md|ideas\.md"),
    ("🧪", r"mutation|pytest|test suite|\bci\b"),
)
_DEFAULT_EMOJI = "🔧"


def _haystack(body, title=""):
    """The text an entry is scored against, opening paragraph weighted x3.

    This journal's voice always leads with the outcome ("The site stops
    going down every time I run"), so the opening is a far better signal
    than a keyword mentioned once in passing four paragraphs down.
    """
    opening = (body or "").split("\n\n", 1)[0]
    return "\n".join([title or "", opening, opening, body or ""]).lower()


def assign_emoji(entries):
    """Give every entry an `emoji`, scoring topics against the whole corpus.

    Raw keyword frequency does not work here, and the way it fails is the
    lesson from Cycle 54 wearing a different costume. Every cycle writes a
    journal, reads the digest and touches the vault -- so "journal",
    "digest" and "vault" appear in all 57 entries and discriminate between
    exactly none of them. Scored naively they won 18 entries outright,
    including ones about outages and heartbeats. A term that matches
    everything tells you nothing about which thing you are looking at.

    So each topic is weighted by how rare it is across the corpus:
    `log(1 + N/df)`. A topic present in every entry keeps a small weight
    rather than zero (with one entry, df == N, and zeroing would leave
    every corpus of one undecidable), while a topic appearing in three
    entries counts for several times as much per match.
    """
    entries = list(entries)
    haystacks = [_haystack(e.get("body", ""), e.get("title", "")) for e in entries]
    total = len(entries)
    weights = []
    for _, pattern in _TOPIC_EMOJI:
        found = sum(1 for text in haystacks if re.search(pattern, text))
        weights.append(math.log(1 + total / found) if found else 0.0)

    for entry, text in zip(entries, haystacks):
        opening = (entry.get("body", "") or "").split("\n\n", 1)[0].lower()
        if _INCIDENT_RE.search(opening):
            entry["emoji"] = _INCIDENT_EMOJI
            continue
        best, best_score = _DEFAULT_EMOJI, 0.0
        for (emoji, pattern), weight in zip(_TOPIC_EMOJI, weights):
            score = len(re.findall(pattern, text)) * weight
            if score > best_score:
                best, best_score = emoji, score
        entry["emoji"] = best
    return entries


def parse_journal(markdown):
    """`journal.md` -> a list of entries in the order they appear (newest first).

    Everything above `## Entries` is the file's own instructions to the
    next cycle, not content, so it is dropped.
    """
    if not markdown:
        return []
    _, marker, body = markdown.partition("\n## Entries")
    text = body if marker else markdown

    headings = list(_ENTRY_HEADING_RE.finditer(text))
    entries = []
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        raw_body = text[start:end].strip()

        pr = outcome = ""
        footer = _FOOTER_RE.search(raw_body)
        if footer:
            pr = footer.group("pr")
            outcome = footer.group("outcome")
            raw_body = raw_body[: footer.start()].rstrip()

        entry = parse_heading(match.group(1))
        label, detail = split_outcome(outcome)
        entry["body"] = raw_body
        entry["pr"] = pr
        entry["prSpans"] = parse_pr_refs(pr)
        entry["outcome"] = label
        entry["outcomeDetail"] = detail
        entries.append(entry)
    return assign_emoji(entries)


def _sections(markdown):
    """`## Heading` -> body text, for a flat two-level markdown file."""
    out = {}
    headings = list(_SECTION_RE.finditer(markdown or ""))
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        out[match.group(1).strip().lower()] = markdown[start:end].strip()
    return out


def parse_digest(markdown):
    """`journal-digest.md` -> its three sections, with the digest lines split out."""
    sections = _sections(markdown)
    needs = sections.get("needs edvard", "")
    lines = []
    for paragraph in re.split(r"\n[ \t]*\n", sections.get("digest", "")):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        match = _DIGEST_LINE_RE.match(paragraph)
        if match:
            text = " ".join(match.group("text").split())
            lines.append(
                {
                    "cycle": int(match.group("cycle")),
                    "at": match.group("at").strip(),
                    "text": text,
                    # Nearly every digest line opens with a bolded sentence
                    # saying what changed, and the card showed the asterisks
                    # -- the one line Edvard was already calling hard to read
                    # was also the only text on the page rendering its own
                    # markup. `text` stays for anything wanting it flat.
                    "spans": render_inline(text),
                }
            )
    return {
        "needsEdvard": needs,
        "hasNeedsEdvard": not is_empty_needs(needs),
        "nextCycle": sections.get("next cycle", ""),
        "lines": lines,
    }


_INLINE_RE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*", re.DOTALL)
_FENCE_RE = re.compile(r"^[ \t]*```")
_BULLET_RE = re.compile(r"^[ \t]*[-*][ \t]+(.*)$")


def render_inline(text):
    """One paragraph -> a list of `{kind, text}` spans.

    Code first in the alternation so a `**` inside backticks stays
    literal. Surveyed against the live journal (2026-08-09): 591 inline
    code spans, 85 bold, zero of anything else -- so this handles what
    the file actually contains rather than markdown in general.
    """
    spans = []
    cursor = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > cursor:
            spans.append({"kind": "text", "text": text[cursor:match.start()]})
        if match.group(1) is not None:
            spans.append({"kind": "code", "text": match.group(1)})
        else:
            spans.append({"kind": "strong", "text": match.group(2)})
        cursor = match.end()
    if cursor < len(text):
        spans.append({"kind": "text", "text": text[cursor:]})
    return spans


def render_blocks(text):
    """Entry body -> a list of blocks, each carrying its inline spans.

    Structured data rather than HTML on purpose: app.js builds every
    node with textContent and never touches innerHTML, so nothing the
    vault contains can become markup. That is a stronger guarantee than
    escaping correctly on every path, and it survives someone later
    pasting a `<script>` into a journal entry.

    Paragraph lines are joined with a space because the journal is
    hard-wrapped at ~95 columns; without this the browser would render
    the author's line breaks as real ones on a phone.
    """
    blocks = []
    paragraph = []

    def flush():
        if paragraph:
            blocks.append({"type": "p", "spans": render_inline(" ".join(paragraph))})
            paragraph.clear()

    lines = (text or "").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCE_RE.match(line):
            flush()
            index += 1
            fenced = []
            while index < len(lines) and not _FENCE_RE.match(lines[index]):
                fenced.append(lines[index])
                index += 1
            blocks.append({"type": "code", "text": "\n".join(fenced)})
            index += 1
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            flush()
            blocks.append({"type": "li", "spans": render_inline(bullet.group(1))})
            index += 1
            continue
        if not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
        index += 1
    flush()
    return blocks


def build_status(entries):
    """The front-page header: is Nova alive, and what did it just do.

    `runningDays` spans the oldest entry to the newest rather than to
    today on purpose -- it answers "how long has this loop been going",
    and if entries stopped arriving a week ago that number should stop
    growing rather than quietly keep counting.
    """
    dated = [e for e in entries if e["date"]]
    numbered = [e for e in entries if e["cycle"] is not None]
    latest = entries[0] if entries else None
    running_days = 0
    if dated:
        from datetime import date

        try:
            newest = date.fromisoformat(dated[0]["date"])
            oldest = date.fromisoformat(dated[-1]["date"])
            running_days = (newest - oldest).days
        except ValueError:
            running_days = 0
    return {
        "cycle": numbered[0]["cycle"] if numbered else None,
        "lastWokeDate": latest["date"] if latest else "",
        "lastWokeTime": latest["time"] if latest else "",
        "lastPr": latest["pr"] if latest else "",
        "lastOutcome": latest["outcome"] if latest else "",
        "lastOutcomeDetail": latest.get("outcomeDetail", "") if latest else "",
        "runningDays": running_days,
        "entryCount": len(entries),
    }
