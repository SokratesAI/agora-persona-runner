"""The menu drawer is grouped, and every page is in it.

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
one happened. `UNLINKED` is where a deliberate omission is named,
so a route quietly missing its link fails instead of shipping unreachable.
It is empty -- `/ask` was the one member and the page is deleted now. The complement matters too: a link to a route the server does
not serve is a 404 in the menu, and that direction is checked as well.

The groups became collapsible `<details>` folds the next morning, on the
owner's follow-up: *"But regarding the menu grouping for pages, good
groups. But i want them to be dropdowns and default closed so i can
navigate more easily and we can easily add more pages of necessary without
expanding the sidebar length too much."* That is the third block of
assertions below. The heading moved from `<h2>` to `<summary>` with it, so
the reader above changed shape; nothing else about the grouping did.

Textual, like `test_nav_drawer_scrolls.py` beside it and for the same
reason -- the browser suite runs under jsdom, which does no layout. What
this file can see is document order and set membership, and both of those
are exactly what the ask is about.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = ROOT / "agora_runner" / "nova_public" / "index.html"
SITE = ROOT / "agora_runner" / "nova_site.py"

#: The pinned rows, in document order. The owner named three on 2026-08-26
#: and added `/projects` on 2026-08-31: *"make the projects link in the Nova
#: sidebar always show above the issues and ideas links as i want to use the
#: projects page more often."* That sentence names Issues and Ideas and does
#: not name Journal, so Projects goes second and Journal keeps the top row.
#: The ordering is the assertion — a `set` here would pass with Projects
#: below Ideas, which is the one arrangement the capture rules out.
PINNED = ["/", "/projects", "/issues", "/ideas"]

#: Routes deliberately reachable without a menu link. Empty on purpose:
#: `/ask` was the only member, and Cycle 759 deleted that page outright on
#: the second half of the owner's ask. A route added here needs a reason
#: beside it.
from agora_runner import nova_site

UNLINKED = set()


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
        r"<a class=\"nav-tab\" href=\"([^\"]+)\"|<summary class=\"nav-group\">([^<]+)</summary>",
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


def test_the_pages_the_owner_named_come_first():
    """Pinned, ungrouped, in their stated order, and nothing else with them.

    My reviewer caught that the first half of this pinned nothing: Journal,
    Issues and Ideas were already the first three anchors before the drawer
    was grouped, so the order assertion alone passed with the whole change
    reverted -- rubric item 1, under a test name that implied otherwise.
    Measured rather than argued: restoring the pre-grouping `index.html` and
    running this file leaves four of six green, this one among them.

    That is no longer true of the order half, and the reason is worth keeping
    rather than deleting the sentence above: `/projects` was inside a fold
    before 2026-08-31, so moving it to second is a change the order assertion
    can see. The second assertion is what makes this test able to fail in
    general, whatever the list happens to be today: **the pinned block is
    exactly `PINNED` and no longer**, so the item immediately after the last
    pinned link is a heading. That boundary is the owner's ("the top 3", then
    a fourth added by name), and an extra link creeping in behind it is the
    way it would quietly go.

    This test was called `test_the_three_the_owner_named_come_first` and said
    "three" in its name, its prose and both of its messages until Cycle 700.
    The assertions read `len(PINNED)` and were right; every word around them
    was wrong the moment the list grew, and a wrong assertion message is what
    a future cycle reads off a red CI run with nobody else in the loop.
    """
    items = _nav_items()
    named = ", ".join(PINNED)
    assert [href for kind, href in items[: len(PINNED)] if kind == "link"] == PINNED, (
        f"the first {len(PINNED)} entries in the drawer must be {named}, in that "
        f"order, with no heading above them. Found: {items[: len(PINNED) + 1]}"
    )
    assert len(items) > len(PINNED) and items[len(PINNED)][0] == "group", (
        f"the pinned block must be exactly those {len(PINNED)} links -- the entry "
        f"after {PINNED[-1]} has to be a category heading. "
        f"Found: {items[len(PINNED) : len(PINNED) + 1]}"
    )


def test_every_other_link_sits_under_a_category():
    """A link after the pinned block with no heading before it is ungrouped."""
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


def test_the_ask_page_is_gone_and_its_api_is_not():
    """The owner's ask in full: *"delete the /ask page entirely as dead code"*.

    The page is deleted in three places and the endpoint behind it is deleted
    in none, because the chat dock is the same thread and talks to the same
    `/api/ask`. Getting that split wrong in either direction is the failure
    this test exists for: leaving the page half-deleted, or taking the dock
    down with it.
    """
    linked = {href for kind, href in _nav_items() if kind == "link"}
    assert "/ask" not in linked, "`/ask` is back in the menu"
    assert "/ask" not in _page_routes(), (
        "`/ask` is back in `PAGE_ROUTES`; the page was deleted, so the server "
        "should 404 it"
    )
    app_js = (HTML.parent / "app.js").read_text(encoding="utf-8")
    assert '"/ask"' not in app_js, (
        "app.js routes `/ask` again -- the client router was the third place "
        "the page lived"
    )
    assert '"/api/ask"' in app_js, (
        "`/api/ask` is gone from app.js -- that is the chat dock's own "
        "endpoint, not part of the deleted page"
    )
    assert '"/api/ask"' in SITE.read_text(encoding="utf-8"), (
        "`/api/ask` is gone from nova_site.py -- the dock has nothing to talk to"
    )


def test_the_chats_page_is_gone_and_the_thread_view_is_not():
    """His capture, 2026-09-01: *"Delete the chats page entirely -- never use it."*

    Same split as `/ask` above and a sharper one, because he drew the line
    himself in the same minute: *"But i use the chat modal all the time!"*
    Three things share the word "chat" here and only the first is deleted --
    the `/conversations` listing, the chat dock, and `/conversation/<id>`,
    which is the URL a push notification opens and what a Beats card opens.
    Taking the dock or the deep link down with the listing would be deleting
    the thing he uses to keep a page he never opened.
    """
    linked = {href for kind, href in _nav_items() if kind == "link"}
    assert "/conversations" not in linked, "the Chats tab is back in the menu"
    assert "/conversations" not in _page_routes(), (
        "`/conversations` is back in `PAGE_ROUTES`; the page was deleted, so "
        "the server should 404 it"
    )
    app_js = (HTML.parent / "app.js").read_text(encoding="utf-8")
    assert '"/conversations"' not in app_js, (
        "app.js routes or navigates to `/conversations` again -- the client "
        "router was the second place the page lived"
    )
    for gone in ("function renderConversations", "function loadConversations",
                 "function renderConvNew"):
        assert gone not in app_js, f"`{gone}` is back -- that is the listing itself"

    # The half that stays. `/conversation/<id>` is a prefix route rather than
    # an entry in `PAGE_ROUTES`, so it is checked against the prefix tuple.
    assert "/conversation/" in nova_site.PAGE_ROUTE_PREFIXES, (
        "`/conversation/<id>` is gone -- that is the URL a push notification "
        "opens, not part of the deleted page"
    )
    assert "function openConversationById" in app_js, (
        "the deep-link opener went with the listing"
    )
    assert '"/api/conversations"' in SITE.read_text(encoding="utf-8"), (
        "`/api/conversations` is gone from nova_site.py -- the chat dock and "
        "the deep link both read it"
    )


def test_every_group_is_a_collapsible_fold():
    """Each heading is a `<summary>` inside its own `<details class="nav-fold">`.

    The owner asked for dropdowns, and the reason a `<details>` is the answer
    rather than a `<div>` with a click handler is that this drawer is the only
    way to reach ten of the thirteen pages: the native disclosure widget is
    keyboard-operable and announces its state without any script running.
    """
    nav = _nav_markup()
    headings = re.findall(r"<summary class=\"nav-group\">([^<]+)</summary>", nav)
    assert headings, "no `<summary class=\"nav-group\">` in the drawer"
    assert not re.search(r"<h2 class=\"nav-group\">", nav), (
        "a group heading is still an `<h2>` -- a heading is not a disclosure "
        "control, so that group cannot be opened or closed"
    )
    folds = re.findall(r"<details class=\"nav-fold\">(.*?)</details>", nav, re.S)
    assert len(folds) == len(headings), (
        f"{len(headings)} group heading(s) but {len(folds)} `<details class=\"nav-fold\">` "
        "block(s) -- every group has to be its own fold"
    )
    for fold in folds:
        assert re.search(r"<summary class=\"nav-group\">", fold), (
            "a `.nav-fold` with no `<summary class=\"nav-group\">` has nothing to tap"
        )


def test_no_fold_is_open_on_load():
    """`default closed` was half the ask, and it is one attribute away from not.

    `markNav` opens the fold holding the current page at runtime; that is
    deliberate and is pinned in the browser suite. What must not happen is a
    fold shipping `open` in the markup, because then it is open on every page.
    """
    stray = re.findall(r"<details[^>]*\bopen\b[^>]*>", _nav_markup())
    assert not stray, (
        f"{stray} ship open in the markup, so that group is expanded on every page -- "
        "the owner asked for default closed"
    )


def test_every_grouped_link_lives_inside_a_fold():
    """A link that sits between two folds is grouped by eye and not by markup.

    `test_every_other_link_sits_under_a_category` reads document order, so a
    link dropped just after a closing `</details>` still passes it: a heading
    precedes it. This reads containment instead, which is what now decides
    whether that link disappears when the group is shut.
    """
    nav = _nav_markup()
    inside = set()
    for fold in re.findall(r"<details class=\"nav-fold\">(.*?)</details>", nav, re.S):
        inside.update(re.findall(r"<a class=\"nav-tab\" href=\"([^\"]+)\"", fold))
    all_links = [href for kind, href in _nav_items() if kind == "link"]
    loose = [h for h in all_links[len(PINNED):] if h not in inside]
    assert not loose, (
        f"{loose} sit outside every `.nav-fold` -- they are grouped in document order "
        "only, so they stay visible when their group is collapsed"
    )
