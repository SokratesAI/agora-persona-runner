"""Ask which research I have already written before I write it again.

`nova/resources/research/` exists so that "no later cycle pays for the same
investigation twice" -- the phrase is written into the documents themselves.
Measured 2026-09-16 over all 1,776 journal entries: 83 documents in that
folder, **6** of them ever named again by a cycle later than the one that
wrote them, and 38 never named in a journal entry at all. The sharpest case
is a pair: Cycle 667 surveyed what else to run on the NAS, and Cycle 669
surveyed it again two cycles later, opening with "this is the answer, so no
later cycle pays for the same survey".

Nothing was broken. A cycle about to research something has no cheap way to
ask what is already there, so it does not ask. This is the cheap way: match
the topic against the **slugs alone**, which needs one listing call and no
document fetches, and a slug in that folder carries its subject
(`nas-linuxserver-survey-2026-08-30`).

Coverage, not overlap, is the score: how much of the *existing* document's
subject the new topic covers. A three-word slug fully covered is a duplicate
however long the new topic is, and a long topic must not be able to dilute
its way under the threshold.
"""

import re

# Dates and their fragments carry no subject: `platform-scan-2026-09-05` is
# about the platform, not about September. Stripping them also stops two
# unrelated documents written the same week from matching each other.
_DATE = re.compile(r"\b(19|20)\d{2}\b|\b\d{1,2}\b")

STOPWORDS = {
    "a", "about", "adding", "against", "an", "and", "any", "are", "as", "at",
    "be", "been", "before", "but", "by", "can", "do", "does", "else", "for",
    "from", "go", "has", "have", "how", "i", "if", "in", "into", "is", "it",
    "its", "just", "me", "my", "no", "not", "of", "on", "one", "or", "our",
    "out", "over", "research", "see", "should", "so", "than", "that", "the",
    "their", "them", "then", "there", "they", "this", "through", "to", "up",
    "was", "we", "what", "when", "where", "which", "why", "will", "with",
    "worth", "would", "you", "your",
}

STRONG = 0.5
"""Coverage at or above this, over at least two shared words, is a duplicate."""


def words(text):
    """The subject words of a slug or a topic sentence, lowercased."""
    text = _DATE.sub(" ", (text or "").lower())
    return [w for w in re.split(r"[^a-z]+", text) if len(w) > 1 and w not in STOPWORDS]


def slug_of(path):
    """`.../research/nas-k3s-2026-08-29.md` -> `nas-k3s-2026-08-29`."""
    name = (path or "").rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name


def related(topic, paths):
    """Existing research ranked by how much of its subject `topic` covers.

    Returns `(coverage, shared, path)` triples, best first, for every
    document sharing at least one subject word. `coverage` is shared over
    the document's own word count, so a short precise slug scores high.
    """
    want = set(words(topic))
    if not want:
        return []
    rows = []
    for path in paths:
        mine = set(words(slug_of(path)))
        if not mine:
            continue
        shared = want & mine
        if not shared:
            continue
        rows.append((len(shared) / len(mine), sorted(shared), path))
    # A one-word slug fully covered ("nas") outscores the real duplicate on
    # coverage alone, so the duplicates sort first whatever their percentage.
    rows.sort(key=lambda r: (not is_strong(r), -r[0], -len(r[1]), r[2]))
    return rows


def is_strong(row):
    coverage, shared = row[0], row[1]
    return coverage >= STRONG and len(shared) >= 2


def render(topic, rows, listed):
    """One block a cycle reads before it starts researching."""
    if not rows:
        return (f"{listed} research documents on file, none of them sharing a "
                f"subject word with {topic!r}.\nNothing written yet — research it.")
    strong = [r for r in rows if is_strong(r)]
    out = []
    if strong:
        out.append(f"ALREADY RESEARCHED — {len(strong)} document(s) cover {topic!r}. "
                   f"Read them before you start; a re-derivation has to say what changed.")
    else:
        out.append(f"Nothing covers {topic!r} outright. {len(rows)} document(s) "
                   f"touch it — worth one look, then research it.")
    for coverage, shared, path in rows[:10]:
        mark = "**" if is_strong((coverage, shared, path)) else "  "
        out.append(f"{mark} {coverage:4.0%}  {slug_of(path)}   ({', '.join(shared)})")
    out.append(f"({listed} research documents on file.)")
    return "\n".join(out)


def index(paths):
    """The whole folder, newest-dated first, one line each."""
    slugs = sorted((slug_of(p) for p in paths), reverse=True)
    return "\n".join(f"  {s}" for s in slugs)
