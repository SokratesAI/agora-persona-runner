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
from datetime import datetime

from agora_runner.config import OSLO

JOURNAL_PATH = "projects/sokrates/projects/agora/nova/journal.md"
# One document per entry, which is where entries live as of 2026-08-09.
# JOURNAL_PATH is the frozen archive it was split out of, and is still
# read as a fallback so the two orderings of "migrate" and "deploy" both
# work -- see `journal_markdown` in nova_site.py.
JOURNAL_DIR = "projects/sokrates/projects/agora/nova/journal/"
DIGEST_PATH = "projects/sokrates/projects/agora/journal-digest.md"
# The digest lines that have rolled off the live file. Same split as the
# journal above and for the same reason: DIGEST_PATH grew to 100KB of
# which 97KB was 54 old digest lines, and a Nova cycle has to read the
# whole thing every hour to get the two short sections at the top. The
# site reads both and concatenates, so nothing a card ever showed
# disappears -- see `digest_markdown` in nova_sources.py. This one lives
# under `resources/` because it is ours; Edvard opens DIGEST_PATH.
DIGEST_ARCHIVE_PATH = "projects/sokrates/projects/agora/nova/resources/digest-archive.md"

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
# The rule is optional, and that is the whole of a bug Edvard could see.
# The review rubric asks every entry to carry a `Reviewer: n findings`
# line, and Cycle 104 wrote it where the rule used to sit -- so the
# entry ended `Reviewer: ...\nPR: #88 | Outcome: merged` with no `---`
# anywhere, the search failed, and its card on the site showed no PR and
# no outcome for a cycle that had merged one. An entry is written once
# and never edited, so the parser is the only end of this that can move.
#
# What actually keeps this off a `---` used as an ordinary rule mid-entry
# is the `$`, not the rule: with no re.MULTILINE it anchors to the end of
# the entry, so the only `PR: ... | Outcome: ...` line that can match is
# the last one. That anchor was always doing the work -- a mutation run
# (2026-08-09) showed as much. It also stops `outcome` from matching a
# single character, since `.+?` is non-greedy: without it `Outcome:
# merged` parses as `m`.
#
# This comment used to add "and adding re.DOTALL here changes nothing at
# all", which was true only while the rule was mandatory and is false now.
# A reviewer demonstrated it: with re.DOTALL, an entry that quotes the
# footer format mid-prose and then ends with its real footer matches the
# *quoted* one, and `outcome` swallows everything from there to the end of
# the entry. No live entry does that today. Do not add the flag.
_FOOTER_RE = re.compile(
    r"\n(?:-{3,}[ \t]*\n)?PR:[ \t]*(?P<pr>.+?)[ \t]*\|[ \t]*Outcome:[ \t]*(?P<outcome>.+?)[ \t]*$",
    re.IGNORECASE,
)
# The repair side of `_FOOTER_RE`, and deliberately a separate pattern:
# that one is anchored to the end of the entry on purpose and must stay
# that way. This one finds a footer *wherever* the cycle put it and
# whether or not it bolded it; `stray_footer` decides whether moving it
# is safe.
_STRAY_FOOTER_RE = re.compile(
    r"\A[ \t]*\*{0,2}[ \t]*PR:[ \t]*(?P<pr>.+?)[ \t]*\|[ \t]*"
    r"Outcome:[ \t]*(?P<outcome>.+?)[ \t]*\*{0,2}[ \t]*\Z",
    re.IGNORECASE,
)
# Not the `_FENCE_RE` further down this file: that one has no capture
# group and is bound *after* this line, so naming this one the same
# silently won the binding and every lookup here raised `no such group`.
# The tests caught it; the collision is worth a sentence because the
# failure was nowhere near the cause. Backticks and tildes both, since a
# closing marker has to match its opening one.
_OPEN_FENCE_RE = re.compile(r"\A[ \t]{0,3}(`{3,}|~{3,})")
_RULE_ONLY_RE = re.compile(r"\A[ \t]*-{3,}[ \t]*\Z")


def stray_footer(body):
    """One entry body -> `(body, pr, outcome)` with a misplaced footer lifted.

    `personality.md` asks for one rigid line at the very end of an entry:
    `PR: ... | Outcome: ...`, bare, under a `---`. Three of the 165 live
    entries do not have it there, and the result is not a parse error --
    their cards render with no PR badge and no outcome at all, which
    reads as a cycle that shipped nothing. Cycles 146 and 147 wrote it
    **bolded, directly under the heading**; entry 004 wrote it correctly
    and hard-wrapped it, so `_FOOTER_RE`'s `$` lands on the continuation.

    The fix people reach for first is loosening `_FOOTER_RE`, and it is
    the wrong one -- its `$` is what stops a `PR: ...` line quoted
    mid-prose from being read as the entry's real outcome, and a reviewer
    has already demonstrated the damage when that anchor gives way. So
    the strict rule stays and the odd shapes are repaired in front of it.

    Three conditions, and each one is refusing to guess:

    - the caller only asks when `_FOOTER_RE` found nothing. An entry that
      ends correctly is never touched, whatever else it contains.
    - candidates inside a fenced code block do not count. `personality.md`
      states the footer format *as* a fenced block, so an entry quoting it
      is a thing a cycle would plausibly write, and a badge invented out
      of an example is worse than no badge.
    - exactly one candidate, or nothing moves. Two means the document is
      making two claims and picking one is inventing an answer.

    A candidate is a whole *paragraph*, not a line, and that is not
    tidiness -- it is the difference between repairing entry 004 and
    corrupting it. A line-at-a-time version of this, run against the live
    folder before any of it was written, matched that entry's first line,
    moved it, and left `survives; next cycle should merge it...` dangling
    at the end of the body under a badge whose outcome stopped
    mid-sentence. Joining the paragraph gives the whole outcome back.
    """
    lines = body.split("\n")
    fenced = [False] * len(lines)
    fence = None
    for index, line in enumerate(lines):
        marker = _OPEN_FENCE_RE.match(line)
        fenced[index] = True if marker else fence is not None
        if marker:
            token = marker.group(1)[0]
            fence = None if fence == token else (fence or token)

    blocks = list(_paragraphs(lines))
    if not blocks:
        return body, "", ""
    ends = (blocks[0][0], blocks[-1][1])

    hits = []
    for start, end in blocks:
        if any(fenced[start:end]):
            continue
        # First paragraph or last, never the middle. The fence guard alone
        # is not enough and a reviewer proved it: `personality.md` states
        # the footer format as a *quotable* example, and this journal
        # quotes rule text as plain unfenced paragraphs constantly, so an
        # entry explaining the rule and then forgetting its own footer got
        # `#23 (or "none")` promoted to a badge -- the exact "invented out
        # of an example" failure this function claims to refuse.
        #
        # An end is where a footer can honestly be. All three live repairs
        # are at one: 146 and 147 wrote it as the opening paragraph, 004
        # as the closing one. A footer-shaped paragraph with prose on both
        # sides of it is somebody quoting the format.
        if start != ends[0] and end != ends[1]:
            continue
        # The footer's own `---` rule shares the paragraph when no blank
        # line separates them, which is how it is actually written.
        head = start + 1 if _RULE_ONLY_RE.match(lines[start]) else start
        if head >= end:
            continue
        match = _STRAY_FOOTER_RE.match(" ".join(lines[head:end]).strip())
        if match:
            hits.append((start, end, match))
    if len(hits) != 1:
        return body, "", ""
    start, end, match = hits[0]
    rest = "\n".join(lines[:start] + lines[end:]).strip()
    return rest, match.group("pr"), match.group("outcome")


def _paragraphs(lines):
    """Line indices of each blank-line-separated block, as `(start, end)`."""
    start = None
    for index, line in enumerate(lines):
        if line.strip():
            start = index if start is None else start
        elif start is not None:
            yield start, index
            start = None
    if start is not None:
        yield start, len(lines)


_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_DIGEST_LINE_RE = re.compile(
    r"^\*\*Cycle[ \t]+(?P<cycle>\d+)\*\*[ \t]*\((?P<at>[^)]*)\)[ \t]*—[ \t]*(?P<text>.*)$",
    re.DOTALL,
)
# One card per cycle, and a blank line is not what decides where a card
# ends -- a `**Cycle N** (` at the start of a line does.
#
# The digest is hand-written by Nova every hour and one cycle dropped the
# blank line between its line and the one below it. Splitting on blank
# lines alone, that is not a formatting slip: the two paragraphs become
# one, `_DIGEST_LINE_RE` matches only the first `**Cycle N**` and swallows
# the second into its `text` (the regex is DOTALL). So Cycle 65 had no
# card on the site at all, and Cycle 66's card ended in Cycle 65's closing
# sentence -- for a day, unnoticed, with every test green. Found
# 2026-08-10 by a reviewer subagent reading this parser against the live
# file, not by the cycle that wrote the file.
#
# The lookahead keeps the blank-line split as well, so a paragraph that
# is not a digest line (the section's own prose) still separates normally.
_DIGEST_SPLIT_RE = re.compile(r"\n[ \t]*\n|\n(?=\*\*Cycle[ \t]+\d+\*\*[ \t]*\()")


def split_digest_entries(text):
    """Where one digest line ends and the next begins -- the only answer.

    Exported rather than kept private because `tools/roll_digest.py` has
    to agree with this file about where a card ends, and for a while it
    did not: it split on blank lines alone, which is the rule the comment
    above records as already broken. The two statements of one rule then
    drifted in the direction that hides itself. A digest holding two cards
    written without a blank line between them shows both on the site and
    counts as *one* entry to the roller, so a file of 14 real cards looks
    like 12, sits under the keep-12 cap, and rolls nothing -- no error, no
    output, the file Edvard reads growing forever, which is the single
    thing that script exists to prevent. Measured before this was moved:
    exactly that, silently.

    So there is one splitter now, and `parse_digest` below is a caller of
    it rather than the owner of it. Anything that needs to know where a
    digest line ends imports this; nothing re-derives it.
    """
    return [p.strip() for p in _DIGEST_SPLIT_RE.split(text) if p.strip()]


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


def _first_paragraph(body):
    """An entry body -> its opening paragraph as one unwrapped line.

    Skips a leading bullet or fenced block so an entry that opens with a
    list still briefs from its first real prose. Lines are joined with a
    space for the same reason `render_blocks` does it: the journal is
    hard-wrapped, and the wrap is not a sentence boundary.
    """
    for chunk in re.split(r"\n[ \t]*\n", body or ""):
        lines = [line.strip() for line in chunk.strip().split("\n") if line.strip()]
        if not lines or _BULLET_RE.match(lines[0]) or _FENCE_RE.match(lines[0]):
            continue
        return " ".join(lines)
    return ""


def parse_journal(markdown, times_by_cycle=None, strip_header=True):
    """`journal.md` -> a list of entries in the order they appear (newest first).

    `strip_header` says whether `markdown` is a whole `journal.md`, whose
    preamble above `## Entries` is the file's own instructions to the next
    cycle rather than content, or an entries body that has no preamble at
    all. **It is a fact about the source and cannot be recovered from the
    text**, which is the whole reason it is a parameter: every entry is
    free prose, this loop's own instructions tell each cycle to write
    about the `## Entries` marker, and an entry that quotes it at the
    start of a line is indistinguishable from the real preamble boundary.
    Passed the folder's assembled entries with the default, the partition
    fires inside somebody's prose and silently drops every *newer* entry
    -- measured 2026-08-13, three entry documents in and one card out, the
    two lost being the two at the top of the feed.

    Guarding on "no `### ` heading above the marker" was tried and does
    not work either: `journal.md`'s preamble documents the entry heading
    format and legitimately contains one
    (`test_a_heading_in_the_preamble_does_not_become_an_entry`).

    So the callers that hold an entries body -- `journal_markdown`, and
    the single-document fetch behind a comment reply -- pass
    `strip_header=False`, and the ones holding a real `journal.md`
    (`split_journal`, `lint_entry`) keep the default.

    `times_by_cycle` (from `entry_times`) overrides the date and time a
    cycle typed into its own `### ` heading with the vault's write time
    for the entry document. Edvard hit this twice: Cycle 86 woke at
    19:00:14, ran seven minutes, and the card said 19:30, because the
    stamp was never measured from anything -- it was a cycle guessing at
    when it expected to finish. The write time is measured, and a heading
    with no cycle number (his own messages, an addendum) keeps its typed
    stamp rather than borrowing someone else's.
    """
    if not markdown:
        return []
    if strip_header:
        _, marker, body = markdown.partition("\n## Entries")
        text = body if marker else markdown
    else:
        text = markdown

    headings = list(_ENTRY_HEADING_RE.finditer(text))
    entries = []
    seen_per_cycle = {}
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
        else:
            raw_body, pr, outcome = stray_footer(raw_body)

        entry = parse_heading(match.group(1))
        cycle = entry["cycle"]
        if times_by_cycle and cycle is not None:
            # A cycle that wrote twice (081 + its addendum) has two files
            # and two entries, both carrying the same cycle number. Both
            # lists are ordered newest-first, so the nth entry for a cycle
            # takes the nth write time rather than all of them collapsing
            # onto one.
            stamps = times_by_cycle.get(cycle) or []
            nth = seen_per_cycle.get(cycle, 0)
            seen_per_cycle[cycle] = nth + 1
            if nth < len(stamps):
                entry["date"], entry["time"] = stamps[nth]
        label, detail = split_outcome(outcome)
        entry["body"] = raw_body
        # The card's brief for the 55 entries that have no digest line --
        # the digest is rewritten every cycle and its older lines are
        # dropped, so that is most of the feed rather than a corner case.
        # Only the brief: their remainder is the journal entry itself, and
        # showing it in both drawers would print the same paragraph twice.
        entry["briefSpans"] = render_inline(split_brief(_first_paragraph(raw_body))[0])
        entry["pr"] = pr
        entry["prSpans"] = parse_pr_refs(pr)
        entry["outcome"] = label
        entry["outcomeDetail"] = detail
        entries.append(entry)
    return assign_emoji(entries)


def entries_body(markdown):
    """The entries half of `journal.md` -- everything below `## Entries`.

    Same split `parse_journal` does, factored out because the migration
    needs it too and the two must not drift: whatever the parser treats
    as content is exactly what gets written out as per-entry documents.
    """
    _, marker, body = (markdown or "").partition("\n## Entries")
    return body if marker else (markdown or "")


def split_entries(markdown):
    """`journal.md` -> [{heading, text}], newest first, in file order.

    `text` starts at the `### ` line and is the entry *verbatim* -- no
    frontmatter, no rewriting. That is what makes the migration provably
    lossless: joining every `text` back together in order reproduces the
    original `## Entries` body byte for byte (modulo the blank lines
    between them), so the split can always be undone from the split
    files alone.
    """
    text = entries_body(markdown)
    headings = list(_ENTRY_HEADING_RE.finditer(text))
    out = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        out.append({
            "heading": match.group(1).strip(),
            "text": text[match.start():end].strip(),
        })
    return out


def entry_filename(seq, heading):
    """`(70, "2026-08-09 22:42 (Oslo) — Cycle 65")` -> `070-cycle-65.md`.

    The sequence number leads because it is the only total order that
    survives: three headings carry no cycle number at all, six cycles
    wrote a second entry, and the dates repeat. Zero-padded so a plain
    lexical sort of the folder is chronological, which is what lets a
    cycle read the newest three without fetching the other sixty-seven.
    """
    cycle = _CYCLE_RE.search(heading or "")
    if cycle:
        slug = f"cycle-{int(cycle.group(1))}"
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", (heading or "").lower()).strip("-")
        # Stripped again after truncating: cutting at 40 characters lands
        # mid-word about as often as not, and `...-not-a-.md` is uglier
        # than the word it saved.
        slug = slug[:40].strip("-") or "entry"
    return f"{seq:03d}-{slug}.md"


def entry_seq(path):
    match = re.match(r"(\d+)", path.rsplit("/", 1)[-1])
    # An unnumbered file sorts oldest rather than being dropped: a
    # hand-added entry should still render, just not jump the queue.
    return int(match.group(1)) if match else -1


def assemble_entries(files):
    """`{path: content}` from `JOURNAL_DIR` -> one newest-first markdown
    blob, shaped exactly like `journal.md`'s **entries half**.

    Not like `journal.md` itself: there is no preamble here, and this
    docstring used to state "so that `parse_journal` cannot tell the two
    sources apart" as the goal. That sentence was the bug. The two sources
    genuinely differ in one respect -- whether a preamble has to be cut
    off the front -- and a parser that cannot tell them apart has to guess
    from the text, which is exactly how an entry quoting `## Entries` in
    its prose deleted every newer card. The caller knows which source it
    holds; `parse_journal`'s `strip_header` is how it says so."""
    ordered = sorted(files.items(), key=lambda kv: (-entry_seq(kv[0]), kv[0]))
    normalised = (normalise_entry(path, content) for path, content in ordered)
    return "\n\n".join(text for text in normalised if text)


_FILE_CYCLE_RE = re.compile(r"-cycle-(\d+)")


def file_cycle(path):
    """The cycle number a `NNN-cycle-M.md` filename claims, or None.

    The filename's claim, never the heading's -- they are two independent
    statements of the same fact and a caller that wants the authoritative
    one has to parse the document. This is only good enough to decide
    *which document to fetch*.
    """
    match = _FILE_CYCLE_RE.search(path.rsplit("/", 1)[-1])
    return int(match.group(1)) if match else None


_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_LEADING_HEADING_RE = re.compile(r"\A#{1,6}[ \t]+(.+?)[ \t]*(?:\r?\n|\Z)")


def normalise_entry(path, content):
    """One entry document -> text `parse_journal` reads as exactly one entry.

    A document is *supposed* to start with its `### ` heading and nothing
    else, and 161 of the 164 live ones do. The three that do not were not
    dropped, which would at least have been visible -- they were absorbed
    into the card above them, because `_ENTRY_HEADING_RE` is what decides
    where an entry begins and text before the first match belongs to
    whatever `assemble_entries` concatenated in front of it. So Cycle
    147's entry rendered as the tail of Cycle 148's card, with no card,
    no permalink and no comment bubble of its own, and Cycle 131's
    frontmatter rendered as literal `---` and `type: log` lines inside
    Cycle 132's. Measured against the live folder 2026-08-13.

    Two shapes, both fixed here rather than in the regex. Loosening
    `_ENTRY_HEADING_RE` to accept `##` would split any entry *body*
    containing a `## ` line, and every entry is free prose, so that trades
    three broken cards for an unknown number of entries chopped in half.

    - a leading heading at the wrong depth (`# Cycle 131 — ...`,
      `## Cycle 146 — ...`) is promoted to `###`, keeping its text: the
      cycle wrote a real heading and only the hash count is wrong.
    - anything else gets one synthesised from the filename, which is the
      only other statement of the entry's identity. `entry_filename`
      built that name *from* the heading, so this is its inverse -- exact
      for the `NNN-cycle-M.md` names every cycle writes, lossy for the
      prose-slug ones, and a lossy card title beats no card. Exactly one
      of the 164 live filenames carries no `-cycle-N` token
      (`004-2026-08-02-edvard-s-first-message-not-a.md`); this said
      "three" until a reviewer checked, which was `entry_filename`'s
      count of headings with no cycle number, copied across without being
      re-measured against the folder this function actually reads.

    Frontmatter is stripped either way. It is not content, no entry that
    parses today has any, and leaving it in front of a promoted heading
    would just move the literal `type: log` lines from one card to
    another.
    """
    text = (content or "").strip()
    if not text:
        return ""
    text = _FRONTMATTER_RE.sub("", text, count=1).lstrip()
    if not text:
        return ""
    if text.startswith("### ") or text.startswith("###\t"):
        return text
    heading = _LEADING_HEADING_RE.match(text)
    if heading:
        body = text[heading.end():].lstrip()
        # `\n\n` rather than `\n`, matching the synthesis branch below and
        # every correctly written document. `parse_journal` strips the body
        # either way, so this is about the assembled markdown staying the
        # shape the rest of the file assumes, not about the parse.
        return f"### {heading.group(1)}\n\n{body}".rstrip() if body else f"### {heading.group(1)}"
    return f"### {synthetic_heading(path)}\n\n{text}"


def synthetic_heading(path):
    """`.../163-cycle-147.md` -> `Cycle 147`; a prose slug back to prose.

    The plain inverse of `entry_filename` -- drop the sequence prefix,
    dashes back to spaces -- and that one rule covers both filename
    shapes rather than special-casing the `-cycle-N` one. It was written
    with a `file_cycle(path)` branch in front of it, which a mutation run
    then showed to be unreachable in any observable sense: `071-cycle-66`
    becomes `Cycle 66` down this path too, so the branch could be deleted
    with every test still green. A branch nothing can distinguish is not
    a safety net, it is a second thing to keep in sync.

    Only ever used for a document that failed to write its own heading,
    so it carries no date or time -- `entry_times` supplies those from
    the document's mtime, which is the authoritative stamp anyway.
    """
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    words = re.sub(r"\A\d+-", "", stem).replace("-", " ").strip()
    return words.capitalize() if words else "Entry"


def entry_times(mtimes):
    """`{path: mtime_ms}` from `JOURNAL_DIR` -> `{cycle: [(date, time), ...]}`.

    Oslo time, because that is what Edvard reads and what the headings
    have always claimed to be. Ordered newest-first per cycle, matching
    the order `assemble_entries` puts the entries in, so `parse_journal`
    can join the two on nothing more than the cycle number both the
    filename (`NNN-cycle-M.md`) and the heading already carry.
    """
    out = {}
    for path, ms in sorted(mtimes.items(), key=lambda kv: (-entry_seq(kv[0]), kv[0])):
        match = _FILE_CYCLE_RE.search(path.rsplit("/", 1)[-1])
        if not match or not ms:
            continue
        stamp = datetime.fromtimestamp(ms / 1000, tz=OSLO)
        out.setdefault(int(match.group(1)), []).append(
            (stamp.strftime("%Y-%m-%d"), stamp.strftime("%H:%M"))
        )
    return out


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
    for paragraph in split_digest_entries(sections.get("digest", "")):
        match = _DIGEST_LINE_RE.match(paragraph)
        if match:
            text = " ".join(match.group("text").split())
            brief, rest = split_brief(text)
            lines.append(
                {
                    "cycle": int(match.group("cycle")),
                    "at": match.group("at").strip(),
                    "text": text,
                    # The two drawers: the headline on the collapsed card,
                    # and the rest of the digest revealed when it opens.
                    # Between them they are the whole line with its bold
                    # rendered rather than shown as asterisks -- which is
                    # why there is no third `spans` field carrying the same
                    # text a second time. #61 stopped reading one when it
                    # split the card into two drawers, and it went on being
                    # sent: a third of every digest line, for nothing.
                    "briefSpans": render_inline(brief),
                    "restSpans": render_inline(rest),
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
# Every item on Edvard's boards opens with his own words as a blockquote,
# and until the board pages existed nothing rendered one -- a `>` line fell
# through to a paragraph and the marker showed on screen as literal text
# in front of the one thing on the page he wrote himself. 66 lines across
# his two files, plus whatever the journal quotes.
_QUOTE_RE = re.compile(r"^[ \t]*>[ \t]?(.*)$")


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


# Edvard, issues.md 2026-08-09: "I need a 2-3 line short precise Digest
# for each cycle as a title for each journey card ... As short as
# possible, max 3 sentences. It should cover everything important ...
# Then, when a journey card is opened, the Digest is revealed."
#
# So a card carries two summaries, not one, and this is the split. Until
# now the collapsed card showed the *whole* digest line clamped by CSS to
# three lines, which is why he asked: a clamp cuts wherever the line box
# ends, so every card trailed off mid-sentence. A brief that ends on a
# sentence is the actual difference.
#
# Derived from the text rather than authored into each entry, for the
# same reason `assign_emoji` is: `## Entries` is append-only (rule 3), so
# the 68 entries that already exist can never be given a new field. A
# `Summary:` footer would only ever brief the future.
#
# The derivation is not a guess, though, which is the part worth knowing.
# The house style for a digest line is a bolded opening sentence saying
# what changed for him -- all 9 live lines have one, every one of them a
# single sentence of 48-179 chars. That sentence *is* the brief, already
# written to be exactly this. Taking whole sentences from the front finds
# it exactly, because a sentence ends after its closing `**`.
#
# `MAX_BRIEF_CHARS` is the one number here, and it is his second
# constraint ("as short as possible") rather than a defensive cap.
# Measured over the 55 entries with no digest line, whose prose was never
# written to be a headline: three unbudgeted sentences run to 633 chars,
# median 349, against 48-179 for the authored ones. 240 sits above every
# authored brief and well under the unbudgeted median. Nothing is
# discarded -- the remainder is the next drawer down, one tap away.
MAX_BRIEF_SENTENCES = 3
MAX_BRIEF_CHARS = 240
# A sentence ends at `.`/`!`/`?`, then any closing emphasis, then space.
# The emphasis run is what makes `...plainly.** Every cycle` break after
# the `**` instead of between the `.` and it, which would split a bold
# span across both drawers and leave the markers unbalanced.
_SENTENCE_END_RE = re.compile(r"[.!?][*_`]*(?=\s|$)")


def split_sentences(text):
    """Text -> sentences, never breaking inside an inline code span.

    Backticks are tracked because the journal quotes shell and paths
    constantly -- `vault_tool.py get 'a.md'` holds two full stops that
    end nothing, and 591 inline code spans across the live file give that
    plenty of chances to happen.
    """
    text = (text or "").strip()
    if not text:
        return []
    sentences = []
    start = 0
    in_code = False
    for index, char in enumerate(text):
        if char == "`":
            in_code = not in_code
            continue
        if in_code:
            continue
        match = _SENTENCE_END_RE.match(text, index)
        if not match:
            continue
        if match.end() <= start:  # inside emphasis already consumed
            continue
        sentences.append(text[start:match.end()].strip())
        start = match.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def split_brief(text):
    """A summary -> `(brief, rest)`, split on a sentence boundary.

    The brief is what a collapsed card shows; the rest is revealed when
    it opens. `rest` is empty when the whole summary already fits, so a
    short digest line does not open onto a blank drawer.
    """
    sentences = split_sentences(text)
    if not sentences:
        return "", ""
    # A digest line whose first sentence is entirely bold has already been
    # written as the headline, so that sentence alone is the brief -- the
    # budget below would otherwise pull a second sentence in after it
    # whenever the headline was short, which is the opposite of "as short
    # as possible". All 9 live digest lines are shaped this way; entries
    # briefed from their own prose have no such marker and fall through.
    if sentences[0].startswith("**") and sentences[0].rstrip().endswith("**"):
        return sentences[0], " ".join(sentences[1:])
    taken = []
    length = 0
    for sentence in sentences[:MAX_BRIEF_SENTENCES]:
        # Always take the first, however long: a brief that is empty
        # because one sentence ran over budget is worse than a long one.
        if taken and length + 1 + len(sentence) > MAX_BRIEF_CHARS:
            break
        taken.append(sentence)
        length += (1 if length else 0) + len(sentence)
    return " ".join(taken), " ".join(sentences[len(taken):])


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
        quote = _QUOTE_RE.match(line)
        if quote:
            flush()
            # Consecutive `>` lines are one quote, joined with a space for
            # the same reason paragraph lines are: the source is wrapped
            # and the reader's screen decides where the breaks go.
            quoted = [quote.group(1).strip()]
            index += 1
            while index < len(lines):
                more = _QUOTE_RE.match(lines[index])
                if not more:
                    break
                quoted.append(more.group(1).strip())
                index += 1
            blocks.append({
                "type": "quote",
                "spans": render_inline(" ".join(part for part in quoted if part)),
            })
            continue
        if not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
        index += 1
    flush()
    return blocks


def _newest_written_at(entries):
    """When the newest entry was written, as an aware datetime, or `None`.

    For a numbered entry the date and time are the vault's *write* time
    for that document rather than the stamp the cycle typed into its own
    heading (`parse_journal` substitutes it), so this is normally a
    measurement and not a cycle's guess at when it expected to finish --
    the same instrument `cycle_health.newest_entry_at` reads, off the same
    mtimes. The substitution is keyed on the cycle number, so an entry
    without one (Edvard's own notes) keeps its typed stamp; that is only
    ever the newest entry in a corpus whose newest entry is not a cycle's,
    and being an hour out on the silence there is not worth reaching for a
    second time source.

    Defensive about the format because the four legacy heading shapes are
    still in the corpus and carry times like `03:19Z`: an entry whose stamp
    will not parse is skipped rather than guessed at, and `None` (no
    usable stamp anywhere) is a different answer from "no silence", which
    is why the caller reports it as `None` instead of `0`.
    """
    for entry in entries:
        if not entry.get("date") or not entry.get("time"):
            continue
        try:
            stamp = datetime.strptime(
                entry["date"] + " " + entry["time"], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        return stamp.replace(tzinfo=OSLO)
    return None


def build_status(entries, known_cycles=None):
    """The front-page header: is Nova alive, and what did it just do.

    `runningDays` spans the oldest entry to the newest rather than to
    today on purpose -- it answers "how long has this loop been going",
    and if entries stopped arriving a week ago that number should stop
    growing rather than quietly keep counting.

    `missingCycles` is the history half of Edvard's #72: *"Nova is 1
    behind agora. Agora failed a cycle Journal and you did not catch
    it."* These are the holes he found himself, by noticing the numbers
    on the feed jump from 126 to 129. Deliberately the whole list and not
    a count -- the feed marks each gap in the position it happened, so
    the numbers are what the client needs, and trimming a list of six to
    spare a header that never renders it would be a limit with no danger
    behind it.

    **`known_cycles` is which cycles have an entry document, and it must
    come from the filenames.** Measured against the live journal
    2026-08-12: 140 cycle numbers appear in `NNN-cycle-M.md` filenames and
    only 137 can be read back out of the `### ` headings inside them,
    because 131 opens with frontmatter and 146 and 147 wrote `## Cycle N`
    with two hashes instead of three. Those three entries exist and are
    not gaps. Inferring the set from parsed headings instead would have
    printed "Cycle 146 ran and wrote no entry" directly above Cycle 146's
    own words -- the exact false alarm #72 warns about, in the one feature
    built to answer it. The filename is also what `cycle_health` counts,
    so passing it here is what actually makes the two agree; sharing
    `gaps_between` only guarantees they agree about a *set*.

    Falls back to the parsed numbers when the caller has none -- the
    frozen `journal.md` archive is one file with no per-entry names, and a
    slightly wrong list there beats no list at all.

    **Everything here is a pure function of the corpus, and the clock is
    deliberately not consulted.** This payload is cached and warmed at
    startup, so a value derived from `now` would freeze at build time and
    could never become true; the live half of #72 (`stalled`) is stamped
    per request in `nova_site.journal_page` instead. `lastWrittenAt` is
    the measured input it needs, carried as a fact rather than a
    judgement so both layers read one clock and one stamp.
    """
    from agora_runner.cycle_health import gaps_between

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
    written_at = _newest_written_at(entries)

    return {
        "cycle": numbered[0]["cycle"] if numbered else None,
        "lastWokeDate": latest["date"] if latest else "",
        "lastWokeTime": latest["time"] if latest else "",
        "lastPr": latest["pr"] if latest else "",
        "lastOutcome": latest["outcome"] if latest else "",
        "lastOutcomeDetail": latest.get("outcomeDetail", "") if latest else "",
        "runningDays": running_days,
        "entryCount": len(entries),
        "missingCycles": gaps_between(
            known_cycles if known_cycles is not None
            else (e["cycle"] for e in numbered)),
        "lastWrittenAt": written_at.isoformat() if written_at else "",
    }
