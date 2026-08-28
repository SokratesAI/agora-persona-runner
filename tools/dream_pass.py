"""Read-only staleness pass over one of Nova's own capture files.

The first slice of the owner's idea #83 -- a dreaming pass over my own
memory, in his words *"merges duplicates, retires what is finished, and
rewrites what has gone stale"*. His own note on the row named the slice
and the safety rule, and both are load-bearing here:

> First slice: run it read-only over `resources/issues.md` alone and see
> what it says is dead.
>
> The design risk worth naming before anyone builds it: a pass that
> rewrites my own memory unsupervised can quietly delete a fact I
> needed, and I would not know, because the evidence would be gone. It
> has to write a proposed diff for a cycle to accept, not edit in place.

So this tool **never writes to the vault and has no `--apply`**. It
reads a file on disk and prints evidence. With `--proposal` it writes a
candidate rewrite to a *second* path so a cycle can `diff` the two and
decide; the original is untouched either way.

Why the target is worth a tool. `nova/resources/issues.md` was 244,329
bytes over 887 lines on 2026-08-23, 536 bullets, and it is read at the
start of every cycle -- so the cost of the mess is charged 36 times a
day. `tools.roll_done_captures` already rolls finished bullets off
*the owner's* two board files; nothing has ever touched mine.

Three signals, and the split between them is the point:

`done`
    The bullet's text begins `DONE (Cycle N):` -- either at the very
    start, or right after the `<date> (Cycle N) —` prefix that every
    bullet in these files opens with. A previous cycle wrote that marker
    deliberately, so this is not a judgement -- it is a label being
    honoured. **This is the only class `--proposal` moves**, verbatim,
    into a `## Retired` section at the end of the file.

    The second of those two shapes was invisible until Cycle 499, which
    is worth knowing before trusting a clean report: the pattern only
    read the very start of the bullet, the house style puts the date
    there, and so the one class this tool may act on had quietly stopped
    matching anything a cycle wrote. A `--proposal` that moves 0 bullets
    is therefore not by itself evidence that a file is tidy.

`dead-path`
    The bullet cites a repo-relative code path in backticks that exists
    in none of the `--repo` checkouts given, **and whose top directory
    one of those checkouts does have**. Evidence that the note describes
    code that has been renamed or deleted; not proof the note is
    worthless, because the *problem* may outlive the file. Advisory:
    reported, never moved.

    What the path means when it is missing is not the same on both
    files, and this signal used to claim it was. On `issues.md` a
    missing path means the code moved. On `ideas.md` it usually means
    the tool being proposed has not been built yet, which is a live idea
    rather than rot -- Cycle 335 measured that and wrote it on the row,
    and Cycle 417 re-measured it: **11 of the 15 flags on my ideas file
    were `tools/<name>.py` proposals**, including the bullet describing
    this very bug. So `--paths-mean` picks the sentence, defaulting off
    the filename, and the count is the same either way; only the reading
    changes.

`cannot-check`
    The bullet cites a path whose top directory is in none of the given
    checkouts -- `deployments/agents/...`, `agora/public/app.js`,
    `nova_public/app.js`. Reported separately because calling these dead
    was a **positive result guaranteed in advance**: with no `agora`
    checkout on the list, `agora/public/app.js` is missing whether or not
    it exists, so the flag measured which repos I passed in and nothing
    about the file. Four of the fifteen flags above were this. Being
    unable to measure is itself the finding (`prompt.md`, "How to work"),
    so the fix is to say so rather than to widen the claim.

`duplicate`
    Two bullets whose normalised word sets overlap above `--similarity`.
    Advisory: reported as a cluster, never moved. Merging two notes is a
    judgement about which sentence to keep, which is exactly what the
    design risk above says a tool must not make on its own.

**Accepting a proposal changes what the Nova site lists**, which is
worth knowing before you write one back to the vault. `nova_boards.
parse_notes` read 530 notes out of `resources/issues.md` before the
first accepted proposal and 516 after -- the difference is exactly the
14 `DONE` bullets, and nothing else moved. That is the intended effect
(`identity.md` rule 8: finished items move to a processed section) and
the text is still in the file, but it is a visible change rather than a
tidy-up nobody sees.

A vault path (`projects/...`, `nova/...`) is never checked for
existence: those live in CouchDB, not in a git checkout, so "not on
disk" would mean nothing about them and flagging one would be a
guaranteed-positive test of the kind `prompt.md` warns about.

    python3 -m tools.dream_pass --file /tmp/nova-issues.md \\
        --repo /data/workspace/agora-persona-runner \\
        --proposal /tmp/nova-issues.proposed.md

**Who runs this, and how often.** Nothing did, from Cycle 334 when it was
built to Cycle 499 when this paragraph was written -- Cycle 417 noticed
the gap 83 cycles in and filed it, and it is the third tool in this loop
to have had that shape. It is owned by the Monday
`Nova — goals & reprioritise` heartbeat now (`weekly-reprioritise.md`),
which was the owner's own instruction on idea #100: *"Make the resources
file cleanup (issues.md/ideas.md staleness pass) run weekly instead of
every cycle."* Weekly is the right cadence for a sweep over files that
gain two bullets an hour, and putting it in a named prompt is what
`tools.doc_owners` can see; a paragraph in the hourly prompt would be
read 72 times a day and acted on by none of them.

Exits 0 whether or not it found anything; a clean file is a result, not
a failure. Exit 1 is a bad argument or an unreadable file.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

# A bullet is a top-level `- ` line plus any indented continuation lines
# under it. Nothing else in these files starts a list.
_BULLET = re.compile(r"^- (?!\s*$)")
# The docstring promises `DONE (Cycle N):` and the first version matched
# `^- DONE\b`, so `- DONE deal, moving on` counted as a retired bullet and
# `--proposal` would have moved it. All 14 markers in the live file carry
# the cycle number -- two of them as `DONE (Cycle 221, runner#212):` -- so
# the tighter pattern loses none of them. Reviewer, runner#296.
#
# Then the convention moved and this pattern did not. `prompt.md` step 6
# says to make a closed bullet "start with `DONE (Cycle N):`", but the
# bullets it also tells every cycle to write open with the date and the
# writing cycle -- `- 2026-08-22 (Cycle 312) — ...` -- so a cycle marking
# one done puts the marker after that prefix, where `^- DONE` cannot see
# it. Measured Cycle 499 on the live file: three bullets are genuinely
# closed in that shape (Cycles 197, 228, 312) and the pattern moved none
# of them, so the one class `--proposal` is allowed to act on had been
# matching nothing for as long as the date prefix has been the house style.
#
# Widening to "contains DONE (Cycle" is the obvious fix and is wrong. Five
# other bullets in the same section quote the marker while *describing* the
# convention -- "reads a closed capture as unprocessed unless the bullet
# starts exactly `DONE (Cycle N):`" -- and retiring those would delete live
# notes about the very mechanism. So the marker still has to be at the head
# of what the bullet says: either at the very start (the old form) or
# immediately after the `<date> (Cycle N) —` prefix and nothing else.
_DATE_PREFIX = r"(?:\d{4}-\d{2}-\d{2} \(Cycle \d+\)\s*[-–—]+\s*)?"
_DONE = re.compile(r"^- " + _DATE_PREFIX + r"(?:\*\*)?DONE \(Cycle \d+")
_CYCLE = re.compile(r"Cycle (\d+)")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Only backticked spans are considered, and only ones shaped like a file:
# no spaces, at least one `/`, and a known source extension. Prose about
# "the poll loop" or "his ideas.md" therefore cannot be flagged, and a
# bare filename with no directory is not enough either -- `cli.py` alone
# is ambiguous across three repos.
_CODE_PATH = re.compile(r"`([A-Za-z0-9_.\-/]+\.(?:py|js|ts|json|ya?ml|toml|cfg|sh))`")

# Paths that are vault documents or absolute, neither of which a repo
# checkout can answer for.
_UNCHECKABLE_PREFIX = ("projects/", "nova/", "/", "resources/")

_WORD = re.compile(r"[a-z0-9_]+")
# Words carried by nearly every bullet in these files. Leaving them in
# makes two unrelated notes look 30% alike before they have said
# anything, which is how a similarity threshold ends up tuned to noise.
_STOP = frozenset("""
a an and are as at be been but by can cycle did do does for from had has have
he i if in into is it its me my no not of on one only or so than that the
their then there these they this to two up was were what when which who will
with would you your it's i've i'd don't
""".split())


class Bullet:
    def __init__(self, index, line_no, lines):
        self.index = index
        self.line_no = line_no
        self.lines = lines

    @property
    def text(self):
        return "\n".join(self.lines)

    @property
    def head(self):
        return self.lines[0]

    @property
    def done(self):
        return bool(_DONE.match(self.head))

    @property
    def cycle(self):
        """The first cycle number on the head line, which is not always the
        cycle that *wrote* the note: a retired bullet reads
        `DONE (Cycle 332): ... (Cycle 331) — ...`, so this returns the cycle
        that closed it. `report` only uses it for a min/max range, where
        either reading is fine; anything else should say which it wants.
        """
        m = _CYCLE.search(self.head)
        return int(m.group(1)) if m else None

    @property
    def date(self):
        m = _DATE.search(self.head)
        return m.group(1) if m else None

    def paths(self):
        out = []
        for p in _CODE_PATH.findall(self.text):
            if p.startswith(_UNCHECKABLE_PREFIX) or "/" not in p:
                continue
            if p not in out:
                out.append(p)
        return out

    def words(self):
        return {w for w in _WORD.findall(self.head.lower())
                if w not in _STOP and len(w) > 2}

    def excerpt(self, width=140):
        one = " ".join(self.head[2:].split())
        return one if len(one) <= width else one[:width - 1] + "…"


def parse(markdown):
    """Every top-level bullet in the document, in file order."""
    lines = markdown.split("\n")
    bullets = []
    current = None
    for n, line in enumerate(lines, start=1):
        if _BULLET.match(line):
            current = Bullet(len(bullets), n, [line])
            bullets.append(current)
        elif current is not None and line.startswith(("  ", "\t")) and line.strip():
            current.lines.append(line)
        else:
            current = None
    return bullets


def resolves(path, repos):
    """Does `path` name a file in any of `repos`?

    Half these notes write the repo name as the first segment --
    `platform-config/deployments/...`, `agora/public/app.js` -- because
    the sentence around them is about which repo the bug is in. Checking
    only `root/path` calls all of those dead, which is a false positive
    generated by my own writing habit rather than by anything being
    stale: giving the tool all four checkouts cut the flags from 16 to 6
    and every one of the six that remained was this. So a first segment
    matching a checkout's own directory name is stripped and retried.
    """
    return any(os.path.exists(os.path.join(root, rel))
               for root, rel in _candidates(path, repos))


def _candidates(path, repos):
    """Every (checkout, path-relative-to-it) this citation could name.

    The repo-name-stripping described in `resolves` lives here so that
    `checkable` applies exactly the same rule; when the two disagreed,
    one of them was answering about a different file.
    """
    for root in repos:
        yield root, path
        head, _, rest = path.partition("/")
        if rest and head == os.path.basename(os.path.normpath(root)):
            yield root, rest


def checkable(path, repos):
    """Can `repos` answer whether `path` exists, either way?

    Only if some checkout holds the path's **top directory**. That is
    the weakest test that still distinguishes the two cases, and the
    weak version is the right one: seeing `tools/` is enough to say this
    repo has no `tools/configmap_script.py`, while nothing on disk here
    can speak about `deployments/agents/newspaper/configmaps.yaml`,
    which lives in a repo I was not given.

    Without it, `dead_paths` flagged the second kind too -- and would
    have flagged it identically had the file been present, which is a
    test whose result was decided before it ran.

    Two ways a checkout can answer, and the first is the one my reviewer
    caught missing. **A citation that names a checkout by its own directory
    name is about that checkout**, and that is the whole reason `resolves`
    strips the prefix -- so `platform-config/deployments/app.yaml` against
    the `platform-config` checkout is answerable even when `deployments/`
    is exactly the directory that got renamed away, which is the *primary*
    rot case rather than an edge one. My first version re-tested the
    stripped remainder's own top directory and filed those as unanswerable,
    which is the opposite of what this split is for.
    """
    for root in repos:
        head, _, rest = path.partition("/")
        if rest and head == os.path.basename(os.path.normpath(root)):
            return True
        top = path.partition("/")[0]
        if top and os.path.isdir(os.path.join(root, top)):
            return True
    return False


def dead_paths(bullets, repos):
    """(bullet, [missing paths], [unanswerable paths]) for each bullet with either.

    With no repos given this returns nothing rather than flagging
    everything -- an unanswerable question is not a positive result.
    """
    if not repos:
        return []
    out = []
    for b in bullets:
        missing, unknown = [], []
        for p in b.paths():
            if resolves(p, repos):
                continue
            (missing if checkable(p, repos) else unknown).append(p)
        if missing or unknown:
            out.append((b, missing, unknown))
    return out


def _similarity(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def duplicates(bullets, threshold):
    """Clusters of bullets whose head lines overlap above `threshold`.

    Single-link clustering: A near B and B near C puts all three in one
    group, because the thing a cycle wants to see is "these say the same
    thing", not every pair that says it.
    """
    words = [b.words() for b in bullets]
    parent = list(range(len(bullets)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(bullets)):
        for j in range(i + 1, len(bullets)):
            if _similarity(words[i], words[j]) >= threshold:
                parent[find(j)] = find(i)

    groups = {}
    for i in range(len(bullets)):
        groups.setdefault(find(i), []).append(bullets[i])
    return [g for g in groups.values() if len(g) > 1]


_RETIRED = "## Retired"


def _retired_line(markdown):
    """1-indexed line of the `## Retired` heading, or None."""
    for n, line in enumerate(markdown.split("\n"), start=1):
        if line.strip() == _RETIRED:
            return n
    return None


def propose(markdown):
    """The file with every `DONE` bullet moved, verbatim, to `## Retired`.

    Returns `(new_markdown, moved_bullets)`. Nothing is deleted, nothing
    is reworded, and a file with no `DONE` bullets comes back byte-identical
    so a second run proposes nothing.
    """
    # Only bullets still in the live part of the file are candidates. A
    # `DONE` bullet that a previous run already moved is still a `DONE`
    # bullet, so without this line the second run moves it again and the
    # file grows a duplicate section every time. Found by the
    # idempotence test, not by reading the code.
    cutoff = _retired_line(markdown)
    bullets = [b for b in parse(markdown) if cutoff is None or b.line_no < cutoff]
    moved = [b for b in bullets if b.done]
    if not moved:
        return markdown, []

    drop = set()
    for b in moved:
        for offset in range(len(b.lines)):
            drop.add(b.line_no + offset)

    lines = markdown.split("\n")
    kept = [line for n, line in enumerate(lines, start=1) if n not in drop]

    body = "\n".join(kept).rstrip("\n")
    if _RETIRED not in body:
        body += "\n\n" + _RETIRED + "\n"
    else:
        body += "\n"
    body += "\n" + "\n".join(b.text for b in moved) + "\n"
    return body, moved


def check(before, after, moved):
    """Assert the proposal moved text and lost none of it.

    The failure mode of rewriting a file this big is silent -- a bullet
    stops appearing and no cycle notices for weeks -- so the proposal is
    verified by parsing both versions and comparing every bullet.

    **As a multiset, not as a set**, and the reviewer on runner#296 is
    why. The first version compared sets and its docstring called that
    the careful choice; it is strictly weaker, and the reviewer built
    the input that breaks it: two byte-identical bullets in, one deleted
    out, `check` returns `None`. A tool whose whole job is finding
    duplicates cannot verify itself with a comparison that collapses
    them. The `moved` loop below counts occurrences under `## Retired`
    for the same reason.
    """
    b_before = Counter(b.text for b in parse(before))
    b_after = Counter(b.text for b in parse(after))
    lost = b_before - b_after
    if lost:
        return "%d bullet(s) vanished from the proposal" % sum(lost.values())
    gained = b_after - b_before
    if gained:
        return ("%d bullet(s) appeared that were not in the original"
                % sum(gained.values()))
    after_retired = after.split(_RETIRED, 1)[-1] if _RETIRED in after else ""
    retired = Counter(b.text for b in parse(after_retired))
    for text, count in Counter(b.text for b in moved).items():
        if retired[text] < count:
            return "a DONE bullet was not moved under %s" % _RETIRED
    return None


#: What a missing path is evidence *of*, keyed by `--paths-mean`. Same
#: bullets, same count -- only the sentence a cycle reads off them changes.
PATHS_MEAN = {
    "rot": "code that has been renamed or deleted",
    "unbuilt": "a tool that has not been built yet — on an ideas file that is "
               "the idea, not rot",
}


def paths_mean_for(filename):
    """Default reading of a missing path, from the name of the file scanned.

    `ideas.md` proposes things; `issues.md` reports them. Guessing off the
    filename is a default and not a decision -- `--paths-mean` overrides it,
    and a cycle scanning a file named neither gets `rot`, the stricter of
    the two, so the flag stays worth reading.
    """
    return "unbuilt" if "idea" in os.path.basename(filename).lower() else "rot"


def report(bullets, repos, threshold, paths_mean="rot"):
    out = []
    done = [b for b in bullets if b.done]
    cycles = [b.cycle for b in bullets if b.cycle is not None]
    out.append("%d bullets, %d marked DONE" % (len(bullets), len(done)))
    if cycles:
        out.append("cycles named: %d..%d" % (min(cycles), max(cycles)))

    out.append("")
    out.append("DONE — a cycle already retired these; --proposal moves them (%d)" % len(done))
    for b in done:
        out.append("  L%-5d %s" % (b.line_no, b.excerpt()))

    flagged = dead_paths(bullets, repos)
    dead = [(b, missing) for b, missing, _ in flagged if missing]
    unknown = [(b, paths) for b, _, paths in flagged if paths]
    out.append("")
    out.append("DEAD PATH (%d) — a checkout I was given covers this path and "
               "does not hold it. Evidence of %s. Advisory only."
               % (len(dead), PATHS_MEAN[paths_mean]))
    for b, missing in dead:
        out.append("  L%-5d %s" % (b.line_no, ", ".join(missing)))
        out.append("        %s" % b.excerpt(110))

    out.append("")
    out.append("CANNOT CHECK (%d) — no checkout I was given covers this path, "
               "so absence here is not evidence either way."
               % len(unknown))
    for b, paths in unknown:
        out.append("  L%-5d %s" % (b.line_no, ", ".join(paths)))
        out.append("        %s" % b.excerpt(110))

    dupes = duplicates(bullets, threshold)
    out.append("")
    out.append("DUPLICATE — word overlap >= %.2f, advisory only (%d cluster(s))"
               % (threshold, len(dupes)))
    for group in dupes:
        out.append("  cluster of %d:" % len(group))
        for b in group:
            out.append("    L%-5d %s" % (b.line_no, b.excerpt(110)))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# The constitution corpus (`--mode constitution`).
#
# The second half of idea #83, and a different shape of rot from the one
# above. `identity.md`, `personality.md` and `prompt.md` are prose, not
# bullets, so nothing here parses; what they carry instead is roughly
# forty citations of *other* documents and modules, and every cycle is
# told to go and read them. `identity.md` says of itself: "If a path here
# ever disagrees with reality again, trust the vault, not this file" --
# a document confessing this exact failure, having already corrected two
# dead paths (`architecture.md`, the ADR folder) and lost a third
# (`kanban.md`) to a deletion it did not hear about.
#
# The measurement that decides how this resolves a citation. I ran it by
# hand first, anchoring every bare filename under `nova/` and
# `nova/resources/`, and it called `resources/architecture.md` dead.
# It is not dead: it lives at `projects/sokrates/projects/agora/resources/
# architecture.md`, one level up, exactly where `identity.md` says it is
# in the same sentence. So a single-anchor resolver reports my own reading
# error as the document's rot, which is worse than not looking -- it puts
# a false correction in front of a cycle that is about to edit its own
# constitution. Hence `_ANCHORS`: a citation resolves if *any* plausible
# anchor holds it, and only a citation no anchor holds is called dead.
#
# Everything is read out of a vault listing passed in on the command line
# (`vault_tool.py ls 'projects/' > listing.txt`), never fetched here. Two
# reasons and both are load-bearing: this module has no vault client and
# should not grow one, and a missing listing then means CANNOT CHECK
# rather than "every path is dead" -- the guaranteed-positive failure
# `dead_paths` above already had to be taught.

# Measured Cycle 569 over the three live files: 59 citations, two of them
# unresolvable. `context/_idea-template.md` is the real one -- `identity.md`
# and `prompt.md` both send a cycle to read it before promoting an idea, and
# no such document has ever existed in the vault, so every promoted idea in
# `resources/ideas/` was written by guessing at the shape. The other is
# `kanban.md`, and it is dead *on purpose*: both files cite it inside the
# sentence recording that the owner deleted it. A deliberately dead citation
# reads identically to rot here, and it stays that way -- a classifier for
# "does the surrounding sentence say this is gone" is a second instrument
# guessing at what I meant, and this tool's whole contract is that a cycle
# decides. Two flags is a list a cycle can read in a second.

_NOVA = "projects/sokrates/projects/agora/nova/"
_AGORA = "projects/sokrates/projects/agora/"

# Ordered widest-plausible-first only for readability; resolution tries all
# of them and stops at the first hit, so the order changes nothing.
_ANCHORS = (
    "",
    _NOVA,
    _NOVA + "resources/",
    _NOVA + "resources/playbooks/",
    _NOVA + "resources/research/",
    _NOVA + "resources/ideas/",
    _NOVA + "context/",
    _AGORA,
    _AGORA + "resources/",
    "projects/sokrates/projects/nova/",
)

# A citation is a backticked span naming a markdown document, a `tools.x`
# module, or a repo-relative `.py` file. Prose about "his ideas.md" is not
# backticked and so cannot be flagged, which is the same restraint
# `_CODE_PATH` above exercises for the bullet corpus.
_DOC_REF = re.compile(r"`([A-Za-z0-9_.<>/\-]+\.md)`")
_MOD_REF = re.compile(r"`tools\.([a-z0-9_]+)`")

# `<seq>-cycle-<n>.md`, `NNN-report-<first>-<last>.md` and friends are
# filename *formats* being described, not files being cited. Resolving one
# is meaningless and calling it dead is a guaranteed positive.
_PLACEHOLDER = re.compile(r"[<>]|^NNN")


def cited_docs(markdown):
    """Every backticked markdown citation in `markdown`, in file order.

    Deduplicated, because these files quote the same filename many times
    and a citation is dead once, not eleven times.
    """
    out = []
    for ref in _DOC_REF.findall(markdown):
        # A leading `./` only. `lstrip` takes a character *set*, so
        # `.lstrip("./")` ate the `...` off the house style's elided
        # citations too -- and `resolve_doc`'s `startswith("...")` branch
        # then became unreachable from here, which no test caught because
        # the test called `resolve_doc` directly with a string this
        # function could no longer produce. A dead branch under a passing
        # test is the shape worth naming: the test was about the function,
        # and the defect was in the seam between two of them.
        if ref.startswith("./"):
            ref = ref[2:]
        if _PLACEHOLDER.search(ref) or not ref:
            continue
        if ref not in out:
            out.append(ref)
    return out


def cited_modules(markdown):
    """Every `tools.<name>` citation in `markdown`, deduplicated."""
    out = []
    for name in _MOD_REF.findall(markdown):
        if name not in out:
            out.append(name)
    return out


def read_listing(path):
    """The set of vault paths in a `vault_tool.py ls` dump."""
    with open(path, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def resolve_doc(ref, listing):
    """The vault path `ref` names, or None if no anchor holds it.

    A citation written with the leading directories elided -- the house
    style writes `.../nova/resources/ideas.md` -- is resolved on its tail,
    since the elision is exactly the part that carries no information.
    """
    # The limitation this design accepts, stated rather than discovered:
    # trying several anchors answers "can a cycle find *a* document by this
    # name", not "is this the right one". `issues.md` and `ideas.md` each
    # exist twice in this vault -- mine under `nova/resources/`, the owner's
    # under `projects/nova/` -- and the constitution cites both on purpose,
    # so this cannot tell a citation of one from a citation of the other.
    # Narrowing it would trade the false positive that matters (a bare
    # filename called dead because I anchored it wrong, which is what a
    # cycle would then go and "fix" in its own constitution) for a false
    # negative that costs nothing, and that is the wrong way round here.
    tail = ref.split("/")[-1] if ref.startswith("...") else ref
    for anchor in _ANCHORS:
        for candidate in (anchor + ref, anchor + tail):
            if candidate in listing:
                return candidate
    return None


def dead_refs(markdown, listing, repos):
    """(dead_docs, dead_modules) -- citations nothing given can resolve.

    With no listing this returns no dead docs rather than all of them,
    and with no checkouts no dead modules, for the reason `dead_paths`
    states: an unanswerable question is not a positive result.
    """
    dead_docs = []
    if listing:
        dead_docs = [r for r in cited_docs(markdown) if not resolve_doc(r, listing)]
    dead_mods = []
    if repos:
        for name in cited_modules(markdown):
            if not resolves("tools/%s.py" % name, repos):
                dead_mods.append(name)
    return dead_docs, dead_mods


def constitution_report(name, markdown, listing, repos):
    docs = cited_docs(markdown)
    mods = cited_modules(markdown)
    dead_docs, dead_mods = dead_refs(markdown, listing, repos)
    out = ["%s — %d document citation(s), %d tools module(s)"
           % (name, len(docs), len(mods))]
    if not listing:
        out.append("  CANNOT CHECK documents — no --vault-listing given, so a "
                   "missing one would look identical to a present one")
    else:
        out.append("  DEAD DOCUMENT (%d) — no anchor in the vault holds this, "
                   "so a cycle told to read it gets [not found]" % len(dead_docs))
        for ref in dead_docs:
            out.append("    %s" % ref)
    if not repos:
        out.append("  CANNOT CHECK modules — no --repo given")
    else:
        out.append("  DEAD MODULE (%d) — cited as tools.<name>, no such file "
                   "in any checkout given" % len(dead_mods))
        for name_ in dead_mods:
            out.append("    tools.%s" % name_)
    return "\n".join(out)


def _constitution(args):
    """`--mode constitution`: report unresolvable citations, write nothing.

    Exits 0 whether or not it found any, the same as the captures pass: a
    clean constitution is a result. Exit 1 is only an unreadable input,
    because a citation this cannot answer for is reported as CANNOT CHECK
    rather than raised.
    """
    if args.proposal:
        print("--proposal has no meaning in --mode constitution: a dead "
              "citation is fixed by writing the document or editing the "
              "sentence, and neither is mechanical", file=sys.stderr)
        return 1
    listing = None
    if args.vault_listing:
        try:
            listing = read_listing(args.vault_listing)
        except OSError as exc:
            print("cannot read %s: %s" % (args.vault_listing, exc), file=sys.stderr)
            return 1
        if not listing:
            print("empty vault listing: %s" % args.vault_listing, file=sys.stderr)
            return 1
    blocks = []
    for path in args.file:
        try:
            with open(path, encoding="utf-8") as fh:
                markdown = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            print("cannot read %s: %s" % (path, exc), file=sys.stderr)
            return 1
        blocks.append(constitution_report(os.path.basename(path), markdown,
                                          listing, args.repo))
    print("\n\n".join(blocks))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, action="append",
                    help="document on disk; fetch it with vault_tool.py first. "
                         "Repeatable in --mode constitution, where the corpus is "
                         "three files rather than one")
    ap.add_argument("--mode", choices=("captures", "constitution"), default="captures",
                    help="'captures' is the bullet pass over issues.md/ideas.md; "
                         "'constitution' is the citation pass over identity.md, "
                         "personality.md and prompt.md")
    ap.add_argument("--vault-listing",
                    help="output of `vault_tool.py ls 'projects/'`, one path per "
                         "line. Without it --mode constitution says CANNOT CHECK "
                         "rather than calling every citation dead")
    ap.add_argument("--repo", action="append", default=[],
                    help="checkout root to resolve cited paths against; repeatable")
    ap.add_argument("--proposal",
                    help="write the candidate rewrite here (never over --file)")
    ap.add_argument("--similarity", type=float, default=0.5,
                    help="word-overlap threshold for the duplicate signal")
    ap.add_argument("--paths-mean", choices=sorted(PATHS_MEAN),
                    help="what a missing path is evidence of; default is "
                         "'unbuilt' for an ideas file, 'rot' otherwise")
    args = ap.parse_args(argv)
    for root in args.repo:
        if not os.path.isdir(root):
            print("no such checkout: %s" % root, file=sys.stderr)
            return 1

    if args.mode == "constitution":
        return _constitution(args)

    # Every other mode reads exactly one file. Taking [-1] instead of
    # refusing would silently ignore the earlier ones.
    if len(args.file) != 1:
        print("--mode captures reads one --file; got %d" % len(args.file),
              file=sys.stderr)
        return 1
    args.file = args.file[0]
    paths_mean = args.paths_mean or paths_mean_for(args.file)

    if args.proposal and os.path.abspath(args.proposal) == os.path.abspath(args.file):
        print("--proposal must not be --file: this tool never edits in place",
              file=sys.stderr)
        return 1
    try:
        with open(args.file, encoding="utf-8") as fh:
            markdown = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError, so the first
        # version tracebacked on exactly the "unreadable file" this line
        # claims to handle. Reviewer, runner#296.
        print("cannot read %s: %s" % (args.file, exc), file=sys.stderr)
        return 1

    # Report the live part of the file only, for the same reason `propose`
    # does: a bullet already under `## Retired` is not something a cycle
    # can act on, and counting it under a heading that says "--proposal
    # moves them" would be a lie the second time this is run.
    cutoff = _retired_line(markdown)
    live = [b for b in parse(markdown) if cutoff is None or b.line_no < cutoff]
    print(report(live, args.repo, args.similarity, paths_mean))

    if args.proposal:
        after, moved = propose(markdown)
        problem = check(markdown, after, moved)
        if problem:
            print("proposal refused: %s" % problem, file=sys.stderr)
            return 1
        try:
            with open(args.proposal, "w", encoding="utf-8") as fh:
                fh.write(after)
        except OSError as exc:
            print("cannot write %s: %s" % (args.proposal, exc), file=sys.stderr)
            return 1
        print("")
        print("proposal written to %s (%d bullet(s) moved) — diff it, do not trust it"
              % (args.proposal, len(moved)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
