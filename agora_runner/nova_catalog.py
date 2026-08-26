"""`nova/catalog.md`, parsed for the `/catalog` page.

Step 2 of the IDP roadmap in `resources/research/idp-2026-08.md`. Step 1
built `tools.catalog`, which reads the live cluster and writes the
catalog into the vault as markdown; until now the only way to read it was
to open Obsidian.

**The page reads the vault file. It does not read the cluster.** That is
the whole design decision and it was made deliberately in the roadmap: a
page with cluster RBAC is a much larger security question than a page
that can read one document, and nothing about showing a catalog needs
it. So the shape here is the boards' shape -- `nova_sources` fetches the
markdown, this module parses it with no I/O of its own, and `nova_site`
turns the result into JSON.

Parsing back out of markdown that this repo also *writes* looks like a
round trip worth avoiding, and it is worth saying why it is not. The
markdown is the artefact: Obsidian renders it, the site renders it, and
`tools.catalog --write` is the one writer. A second machine-readable
file beside it would be the two-copies-of-one-constant failure this repo
has already filed against itself three times, with the added twist that
one copy would be the one the owner reads.

The columns and the three status words come from `tools.catalog.render`,
and `tests/test_nova_catalog.py` parses that function's own live output
rather than a hand-written fixture -- so a column added there fails here
instead of quietly rendering a blank cell.
"""

import re

CATALOG_PATH = "projects/sokrates/projects/agora/nova/catalog.md"

# `tools.catalog.render` writes exactly these, per service: ready, scaled
# to zero on purpose, or wanted and not ready. "off" and "NO" are very
# different facts and the page must not draw them the same way.
STATUS = {"yes": "up", "off": "off", "NO": "down"}

_ROW = re.compile(r"^\|(.+)\|\s*$")
_LINK = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
# `maintenance:` in the frontmatter carries the only provenance the file
# has: when it was last built, and -- while a cycle was the only thing that
# ever built it -- which cycle. A stale catalog is the failure this page
# exists to make visible, so it belongs on the page, not just in the file.
#
# Two forms, because step 3 (Cycle 451) took the rebuild off the cycles: the
# runner refreshes it hourly now and has no cycle number to write, so the
# builder writes a plain "Last regenerated <when>." The cycle form is still
# matched, and not only for the archive -- `--publish` by hand and the timer
# write the same document today, but an entry that predates the timer is
# still a real reading of the cluster and the page should date it correctly
# rather than call it undated.
_REGENERATED_CYCLE = re.compile(r"Cycle (\d+) \(last regenerated ([^)]+)\)")
_REGENERATED = re.compile(r"[Ll]ast regenerated ([^.]+?)\s*\.")


def _cells(line):
    inner = _ROW.match(line)
    if not inner:
        return None
    return [c.strip() for c in inner.group(1).split("|")]


# `*source repo*` and `` `GitHubService` `` are how the catalog's prose
# reads in Obsidian, and the page draws text rather than markdown -- so on
# the screen they were an asterisk and a backtick, literally. Caught by
# opening the real page and looking at it, which is the only check that
# could have caught it: every test I wrote asserts on the string, and the
# string was correct.
_EMPHASIS = re.compile(r"[*`_]{1,2}(?=\S)|(?<=\S)[*`_]{1,2}")


def _plain(text):
    """Markdown emphasis stripped, the words kept.

    Deliberately only the three inline markers `tools.catalog` actually
    writes. A general markdown renderer here would be a second one on
    this site -- `app.js` already has the journal's -- and this is one
    paragraph of prose, not a document.
    """
    return _EMPHASIS.sub("", text)


def _split_lead(paragraph):
    """`**bold sentence** the rest` -> `(bold sentence, the rest)`.

    `tools.catalog` writes its headline as one bolded claim followed by
    the qualification, and they carry different weight: the first is the
    number, the second is why the number is what it is. The page draws
    them differently, so they arrive as two strings rather than as one
    with markup in it -- nothing on this site renders markdown from the
    catalog, and a literal `**` on the screen is what that would look
    like.
    """
    body = paragraph[2:]
    close = body.find("**")
    if close == -1:
        return _plain(body.strip()), ""
    return _plain(body[:close].strip()), _plain(body[close + 2:].strip())


def _service(cells):
    name, namespace, claim, deployed, url, up = cells[:6]
    link = _LINK.match(url)
    return {
        "name": name,
        "namespace": namespace,
        # An em dash is how the markdown says "nothing"; the page wants an
        # absence it can test, not a character it has to know about.
        "claim": None if claim == "—" else claim,
        "deployedBy": None if deployed == "—" else deployed,
        "host": link.group(1) if link else None,
        "url": link.group(2) if link else None,
        "status": STATUS.get(up, "unknown"),
    }


def parse_catalog(markdown):
    """The catalog as the page gets it.

    An empty or missing file parses to an empty catalog with `missing`
    set rather than raising: the vault legitimately has no catalog until
    a cycle runs the tool, and a page saying so is more useful than a 500.
    """
    text = markdown or ""
    lines = text.splitlines()
    services = []
    doors = []
    unreadable = []
    headline = ""
    detail = ""
    incomplete = False
    regenerated = None
    cycle = None

    in_table = False
    in_doors = False
    for line in lines:
        stripped = line.strip()
        if regenerated is None:
            found = _REGENERATED_CYCLE.search(stripped)
            if found:
                cycle = int(found.group(1))
                regenerated = found.group(2)
            else:
                found = _REGENERATED.search(stripped)
                if found:
                    regenerated = found.group(1)
        if stripped.startswith("**"):
            # The first bold paragraph is the headline either way, but the
            # two say opposite things: one is a coverage number, the other
            # is a refusal to give one. The page has to be able to tell.
            if not headline:
                headline, detail = _split_lead(stripped)
                incomplete = stripped.startswith("**Incomplete")
            continue
        if stripped.startswith("## "):
            in_doors = stripped.lower().startswith("## doors")
            continue
        if stripped.startswith("|"):
            cells = _cells(stripped)
            if not cells or set("".join(cells)) <= {"-", " "}:
                # The header row and the `|---|` separator. `in_table`
                # flips on the separator, so a stray pipe in prose above
                # the table cannot be read as a service.
                in_table = cells is not None and set("".join(cells)) <= {"-", " "}
                continue
            if in_table and len(cells) >= 6:
                services.append(_service(cells))
            continue
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if in_doors:
                doors.append(item)
            elif incomplete and not services:
                # The bullets under an `**Incomplete**` headline are the
                # sources that could not be read. They sit above the
                # table, which is why "before any service row" is what
                # separates them from anything else bulleted.
                unreadable.append(item)
            continue

    return {
        "headline": headline,
        "detail": detail,
        "incomplete": incomplete,
        "unreadable": unreadable,
        "services": services,
        "doors": doors,
        "cycle": cycle,
        "regenerated": regenerated,
        "missing": not text.strip(),
    }


def catalog_page(payload):
    """What `/api/catalog` sends. A pass-through today, plus the counts.

    The counts are computed here rather than in `app.js` for the reason
    every other page in this repo does it: the page draws what the server
    says, so a number on the screen has one definition and one test.
    """
    services = payload.get("services", [])
    page = dict(payload)
    page["total"] = len(services)
    page["down"] = sum(1 for s in services if s["status"] == "down")
    page["off"] = sum(1 for s in services if s["status"] == "off")
    return page
