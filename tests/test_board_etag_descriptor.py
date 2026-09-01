"""`/api/board`'s etag varies by every argument `board_page` reads.

This is the enforcement half of `board_descriptor`. That function derives
the etag variant from the argument dict the call was made with, so a
parameter cannot reach `board_page` without reaching the etag -- but only
as long as the dict really is the full signature. The failure being
pinned is not a typo. It is a fifth parameter added to `board_page` six
months from now by someone who never opens `_send_board`, and the symptom
it produces is a 304 carrying somebody else's rows, which looks exactly
like a working cache.

That is what happened to the version this replaces: three hand-written
variants, one per branch, and the `q=` one shipped without `mine`. Two
tabs, two boards that share a number space, one cache entry.
"""

import inspect

from agora_runner.nova_site import NovaSiteHandler, board_descriptor, board_page

# The arguments `_send_board` builds, with the defaults it builds them
# from. Kept here rather than imported so that a change to the endpoint
# has to be made in two places on purpose instead of one by accident.
CALL = {"item": None, "limit": None, "search": None, "mine": False}


def test_descriptor_covers_every_parameter_board_page_takes():
    """The coupling. Add a parameter to `board_page` and this fails.

    This one is a *forward* guard and not a regression test, and saying
    so is the honest label: it compares `board_page`'s signature against
    `CALL` and would pass just as happily against the three hand-written
    variants this change replaces, because that signature is not what the
    change touched. The regression half is
    `test_send_board_builds_that_dict_and_derives_from_it` below. Both
    are needed and neither substitutes for the other -- this one catches
    the parameter added in six months, that one catches the endpoint
    being written back the old way.
    """
    taken = [
        name
        for name, param in inspect.signature(board_page).parameters.items()
        if param.kind is not param.VAR_KEYWORD and name != "payload"
    ]
    assert sorted(taken) == sorted(CALL), (
        "board_page's signature and the dict _send_board passes it have "
        "drifted; a parameter outside board_descriptor's dict is a "
        "parameter the etag does not vary by"
    )


def test_send_board_builds_that_dict_and_derives_from_it():
    """The regression half: revert the endpoint and this fails.

    Read off the source rather than by calling it, because calling it
    needs a live handler, a socket and a payload cache. The assertion is
    narrow on purpose -- it is that the endpoint hands `board_page` a
    `**`-expanded dict and hands `board_descriptor` the same name.

    `inspect.getsource` on the **method**, not on the module split at a
    substring. The first version of this test did the latter, and a
    reviewer took it apart: `source.split("def _send_board", 1)` matches
    a prefix rather than a name, so a future `_send_board_summary`
    defined earlier in the file would be silently inspected instead, and
    a rename would surface as an `IndexError` rather than as a failure
    that says what is wrong. Asking for the attribute gets an
    `AttributeError` naming the method that went missing, which is the
    thing a reader needs to know.
    """
    body = inspect.getsource(NovaSiteHandler._send_board)
    assert "board_page(payload, **args)" in body
    assert "board_descriptor(args)" in body
    for name in CALL:
        assert f'"{name}"' in body, f"_send_board no longer builds {name}"


def test_every_argument_changes_the_descriptor():
    """Vary one key at a time; each must move the string.

    `mine` is the one that matters and the one that was wrong. It is also
    the one a reader is most likely to talk themselves out of, because
    the list branch does not read it -- so it is asserted alongside the
    three nobody doubts, at the same strength.
    """
    base = board_descriptor(CALL)
    for name, other in (
        ("item", 57),
        ("limit", 12),
        ("search", "badge"),
        ("mine", True),
    ):
        varied = board_descriptor(dict(CALL, **{name: other}))
        assert varied != base, f"descriptor ignores {name}"


def test_a_typed_value_never_collides_with_text_that_looks_like_it():
    """`repr`, not `str` -- and this test is the second version of itself.

    The first one asserted that `None`, `0`, `False` and `""` stay four
    entries and I mutated `!r` to nothing to check it. It passed:
    `str()` maps those to `'None'`, `'0'`, `'False'` and `''`, which are
    already four different strings, so the test failed under no mutation
    at all and would have shipped pinning nothing.

    What `repr` actually buys is the case where a *value* collides with
    another value's text. `search` is the free-text box, so "None" is a
    thing the owner can type, and under `str()` searching for it and not
    searching at all hash to `search=None` either way -- one cache entry
    over a list of matches and an empty list.
    """
    no_search = board_descriptor(CALL)
    searching_for_the_word = board_descriptor(dict(CALL, search="None"))
    assert no_search != searching_for_the_word

    # The same trap on the other free-text-adjacent pair: an empty query
    # matches nothing and no query at all returns the list, and they must
    # not share an entry either.
    assert board_descriptor(dict(CALL, search="")) != no_search


def test_descriptor_is_stable_across_key_order():
    """Two dicts with the same contents hash the same, whatever the order.

    Python preserves insertion order, so a refactor of `_send_board` that
    builds the same four keys in a different order would otherwise turn
    over every client's etag for no reason at all.
    """
    forward = board_descriptor({"item": 3, "limit": 5, "search": "x", "mine": True})
    backward = board_descriptor({"mine": True, "search": "x", "limit": 5, "item": 3})
    assert forward == backward
