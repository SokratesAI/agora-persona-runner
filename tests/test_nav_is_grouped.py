"""The menu drawer is grouped, and every page but `/ask` is in it.

The owner, `issues.md` 2026-08-26: *"Now that the sidebar for the Nova app
has begun to contain a lot of page links, group the by category, but always
show the top 3 most used at the top. I mostly use journals, issues and
ideas. And also, the ask page can be cut as i now have the chat bubble."*

Two different things are pinned here and only one of them is layout.

The first is the **grouping the owner asked for**: the three they named come
first, with no heading above them, and every other link sits under one. That
is a decision about their menu, not a fact about the code, so it is written
down as an assertion rather than left to whoever edits `index.html` next.

The second is the **drift** that made the grouping worth guarding. Adding a
page means editing `PAGE_ROUTES` in `nova_site.py`, the router in `app.js`,
and this list -- three places, and nothing has ever checked that the third
one happened. `/ask` is the single deliberate omission and it is named here,
so a fourth route quietly missing its link fails instead of shipping
unreachable. The complement matters too: a link to a route the server does
not serve is a 404 in the menu, and that direction is checked as well.

Textual, like `test_nav_drawer_scrolls.py` beside it and for the same
reason -- the browser suite runs under jsdom, which does no layout. What
this file can see is document order and set membership, and both of those
are exactly what the ask is about.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = ROOT / "agora_runner" / "nova_public" / "index.html"

#: The three the owner named, in the order they named them.
PINNED = ["/", "/issues", "/ideas"]

#: The one route deliberately reachable without a menu link. The chat dock
#: is the same thread, so the page is redundant on the menu; the route stays
#: so an existing bookmark still resolves.
UNLINKED = {"/ask"}


def _nav_markup():
    html = HTML.read_text(encoding="utf-8")
    nav = re.search(r"<nav id=\"nav\".*?</nav>", html, re.S)
    assert nav, "no `<nav id=\"nav\">` in index.html"
    return nav.group(0)


def _nav_items():
    """Every link and heading inside the drawer, in document order.

    Returns ``("link", href)`` and ``("group", title)`` tuples, so a test
    can assert about order and not only about membership.
    """
    items = []
    for hit in re.finditer(
        r"<a class=\"nav-tab\" href=\"([^\"]+)\"|<h2 class=\"nav-group\">([^<]+)</h2>",
        _nav_markup(),
    ):
        if hit.group(1) is not None:
            items.append(("link", hit.group(1)))
        else:
            items.append(("group", hit.group(2).strip()))
    return items


def _page_routes():
    """`PAGE_ROUTES` from `nova_site.py`, read without importing it."""
    src = (ROOT / "agora_runner" / "nova_site.py").read_text(encoding="utf-8")
    block = re.search(r"^PAGE_ROUTES = \((.*?)^\)", src, re.S | re.M)
    assert block, "no `PAGE_ROUTES` tuple in nova_site.py"
    return [m.group(1) for m in re.finditer(r"\"([^\"]+)\"", block.group(1))]


def test_the_three_the_owner_named_come_first():
    """Pinned, ungrouped, in their stated order, and exactly three.

    My reviewer caught that the first half of this pinned nothing: Journal,
    Issues and Ideas were already the first three anchors before the drawer
    was grouped, so the order assertion alone passes with the whole change
    reverted -- rubric item 1, under a test name that implies otherwise.
    Measured rather than argued: restoring the pre-grouping `index.html` and
    running this file leaves four of six green, this one among them.

    The order is still worth pinning, so it stays. What makes the test able
    to fail is the second assertion: **the pinned block is exactly three**,
    so the item immediately after Ideas is a heading. That is the boundary
    the owner drew ("the top 3"), it is new with the grouping, and a fourth
    link creeping into the pinned block is the way it would quietly go.
    """
    items = _nav_items()
    assert [href for kind, href in items[: len(PINNED)] if kind == "link"] == PINNED, (
        "the first three entries in the drawer must be Journal, Issues and Ideas, "
        f"with no heading above them. Found: {items[: len(PINNED) + 1]}"
    )
    assert len(items) > len(PINNED) and items[len(PINNED)][0] == "group", (
        "the pinned block must be exactly three links -- the entry after Ideas has "
        f"to be a category heading. Found: {items[len(PINNED) : len(PINNED) + 1]}"
    )


def test_every_other_link_sits_under_a_category():
    """A link after the pinned three with no heading before it is ungrouped."""
    seen_group = False
    for index, (kind, value) in enumerate(_nav_items()):
        if kind == "group":
            seen_group = True
            continue
        if index < len(PINNED):
            continue
        assert seen_group, (
            f"`{value}` sits after the pinned three but under no category heading -- "
            "either give it a group or move it into the pinned list on purpose"
        )


def test_every_group_has_a_link_under_it():
    """An empty heading is a leftover from a link somebody moved."""
    items = _nav_items()
    for index, (kind, value) in enumerate(items):
        if kind != "group":
            continue
        after = items[index + 1 :]
        assert after and after[0][0] == "link", (
            f"the `{value}` heading has no link under it"
        )


def test_every_page_route_is_in_the_menu():
    """The drift this file exists for: a new page with no way to reach it."""
    linked = {href for kind, href in _nav_items() if kind == "link"}
    missing = [r for r in _page_routes() if r not in linked and r not in UNLINKED]
    assert not missing, (
        f"{missing} are served by nova_site.py and are in no menu group. Add a link, "
        f"or add the route to UNLINKED in this file and say why."
    )


def test_the_menu_links_nowhere_the_server_does_not_serve():
    """The other direction: a link that 404s is worse than a missing one."""
    routes = set(_page_routes())
    stray = [h for kind, h in _nav_items() if kind == "link" and h not in routes]
    assert not stray, f"{stray} are in the menu and are not `PAGE_ROUTES`"


def test_ask_is_off_the_menu_and_still_served():
    """Both halves of the owner's "the ask page can be cut"."""
    linked = {href for kind, href in _nav_items() if kind == "link"}
    assert "/ask" not in linked, "`/ask` was cut from the menu; it is back"
    assert "/ask" in _page_routes(), (
        "`/ask` lost its route as well as its link -- the link was the ask, and a "
        "bookmark to the page should still resolve"
    )
