"""Every POST route this server dispatches must also be on its own allowlist.

`do_POST` refuses anything outside a hardcoded tuple before it reads the
body, and then dispatches on `path ==` further down. Those are two lists of
the same thing, written 15 lines apart, and adding a route to the second and
not the first produces a feature that is completely unreachable while looking
completely finished: the page renders, the button works, the request goes out,
and the server answers 404 before any handler runs.

That is not hypothetical. Cycle 441 shipped `/api/conversations/send` and
`/api/conversations/new` with the dispatch arms and without the allowlist
entries, and merged it -- 3,524 Python tests and 508 browser tests green,
because the Python tests call the module functions directly and the browser
tests mock `fetch`. Neither side crosses `do_POST`. My reviewer found it
thirty seconds after the merge.

The fix for the *shape* is this file rather than two more allowlist entries:
the two lists are derived from the same source and compared, so the next
route to miss one fails here instead of on his phone.
"""
import ast
import inspect

import agora_runner.nova_site as nova_site


def _do_post_tree():
    source = inspect.getsource(nova_site.NovaSiteHandler.do_POST)
    return ast.parse(source.lstrip() if source.startswith(" ") else source)


def _dispatched_paths(tree):
    """Every literal in a `if path == "<literal>":` test inside `do_POST`."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "path"):
            continue
        right = node.comparators[0]
        if isinstance(right, ast.Constant) and isinstance(right.value, str):
            found.add(right.value)
        # `if path in ("/api/capture/edit", "/api/capture/delete")` is a
        # dispatch too -- handled below by the `In` walk.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.In):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "path"):
            continue
        right = node.comparators[0]
        if isinstance(right, ast.Tuple):
            found |= {e.value for e in right.elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return found


def _allowlisted_paths(tree):
    """The `if path not in (...)` gate's tuple."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.NotIn):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "path"):
            continue
        right = node.comparators[0]
        if isinstance(right, ast.Tuple):
            return {e.value for e in right.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    raise AssertionError("do_POST no longer has a `path not in (...)` gate")


def test_every_dispatched_post_route_is_reachable():
    tree = _do_post_tree()
    allowed = _allowlisted_paths(tree)
    # The `in`-tuple dispatches are also read by `_allowlisted_paths`' walk
    # partner, so subtract the gate's own tuple before comparing.
    dispatched = _dispatched_paths(tree) - allowed
    # `/api/upload` and `/mcp` are answered above the gate on purpose --
    # `_post_upload` reads its own body past MAX_BODY_BYTES, and `/mcp` is
    # a different protocol. Both are `path ==` arms that legitimately are
    # not allowlisted.
    dispatched -= {"/api/upload", "/mcp"}
    assert dispatched == set(), (
        "these POST routes are dispatched but answered 404 before the "
        "dispatch is reached, so the feature behind each is unreachable: "
        + ", ".join(sorted(dispatched)))


def test_the_allowlist_carries_nothing_it_cannot_dispatch():
    """The other direction. A path allowlisted and never dispatched reads
    the body, falls through and answers 404 anyway -- harmless, but it is a
    route that looks supported in the one place a reader would check."""
    tree = _do_post_tree()
    allowed = _allowlisted_paths(tree)
    dispatched = _dispatched_paths(tree)
    # `/api/capture` is the fall-through at the bottom of `do_POST` -- every
    # `path ==` arm returns, and whatever is left is a capture. So it is
    # allowlisted and correctly has no arm of its own.
    orphans = allowed - dispatched - {"/api/capture"}
    assert orphans == set(), (
        "allowlisted but never dispatched: " + ", ".join(sorted(orphans)))
