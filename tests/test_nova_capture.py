"""The capture box: where a bullet lands, and what happens when the write loses.

The two fixtures are the live `issues.md` and `ideas.md` copied verbatim
down to their first heading, on 2026-08-09. Everything below that heading
is omitted rather than trimmed for taste: `insert_captures` stops scanning
at the first `#` line by construction, so the region it can read is
complete here. The pair is kept because they genuinely differ -- `issues.md`
has a blank line between the frontmatter and the capture bullet and
`ideas.md` does not -- which is exactly the difference that would make an
offset-based insertion correct on one file and wrong on the other.
"""

import difflib
import os
import re
from unittest.mock import patch

import pytest

from agora_runner import nova_capture
from agora_runner.nova_boards import _captures
from agora_runner.nova_capture import (
    CAPTURE_TARGETS,
    amend,
    capture,
    clean_capture_text,
    STALE_CAPTURE,
    capture_entries,
    comment_on_capture,
    convert_capture,
    insert_captures,
    list_captures,
    replace_capture,
    reply_under_capture,
)
from agora_runner import vault
from tests.couch_fake import FakeCouch

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def issues_md():
    return _fixture("issues_capture_sample.md")


@pytest.fixture
def ideas_md():
    return _fixture("ideas_capture_sample.md")


def _capture_list(markdown):
    """The bullets above the first heading -- the list the owner writes in."""
    out = []
    for line in markdown.split("\n"):
        if line.strip().startswith("#"):
            break
        if line.strip() == "-" or line.strip().startswith("- "):
            out.append(line)
    return out


# --- text -> bullets ------------------------------------------------------


def test_a_typed_line_becomes_one_bullet():
    assert clean_capture_text("the app needs a restart") == ["the app needs a restart"]


def test_each_line_of_a_paste_becomes_its_own_bullet():
    assert clean_capture_text("first thing\nsecond thing") == ["first thing", "second thing"]


def test_blank_lines_and_surrounding_space_are_dropped():
    assert clean_capture_text("  first  \n\n\n  second\n") == ["first", "second"]


@pytest.mark.parametrize("typed", ["- already a bullet", "-   already a bullet"])
def test_typing_the_bullet_character_does_not_double_it(typed):
    """Otherwise the file grows `- - already a bullet`."""
    assert clean_capture_text(typed) == ["already a bullet"]


def test_crlf_from_a_phone_keyboard_does_not_leave_stray_carriage_returns():
    assert clean_capture_text("first\r\nsecond\rthird") == ["first", "second", "third"]


@pytest.mark.parametrize("empty", ["", "   ", "\n\n", "-", "- "])
def test_nothing_typed_is_no_bullets(empty):
    assert clean_capture_text(empty) == []


# --- an attached image belongs to the text it was attached to -------------
#
# the owner, capture 2026-08-21: "I see that my image upload test was split
# into two idea entries. The image for its own separate entry and the text
# got the other." `buildAttach` inserts the link as its own paragraph, so
# the line rule above filed his sentence and his screenshot separately.

IMG = "![shot.jpg](/api/upload/89f92e607e3e8a3e85a40b40f4a07609.jpg)"


def test_an_attached_image_stays_on_the_bullet_it_was_attached_to():
    assert clean_capture_text("testing image upload\n\n" + IMG) == [
        "testing image upload " + IMG
    ]


def test_two_attached_images_both_ride_along():
    other = "![b.png](/api/upload/00112233445566778899aabbccddeeff.png)"
    assert clean_capture_text("look\n\n" + IMG + "\n" + other) == [
        "look " + IMG + " " + other
    ]


def test_a_real_filename_with_brackets_in_it_still_folds():
    """`buildAttach` puts `file.name` into the alt text verbatim.

    Android and Windows hand back names like `photo (1).jpg` all the time,
    and a fixture of clean `shot.jpg` names would never exercise that. The
    parentheses sit inside the alt text, which is `[^\\]]*`, so they are
    fine -- this test is what says so rather than my reading of the regex.
    """
    link = "![photo (1).jpg](/api/upload/89f92e607e3e8a3e85a40b40f4a07609.jpg)"
    assert clean_capture_text("here it is\n\n" + link) == ["here it is " + link]


def test_a_filename_containing_a_square_bracket_does_not_fold():
    """Known limit, written down rather than discovered later.

    `app.js`'s own `ATTACH_RE` cannot match this either, so the line does
    not render as an image on any surface — it degrades the same way on
    both sides instead of one of them silently disagreeing. The picture is
    still captured, as its own bullet; nothing is lost but the pairing.
    """
    link = "![photo[1].jpg](/api/upload/89f92e607e3e8a3e85a40b40f4a07609.jpg)"
    assert clean_capture_text("here it is\n\n" + link) == ["here it is", link]


def test_attaching_before_typing_still_files_one_capture():
    """He taps attach with an empty box, then types -- his complaint mirrored.

    Reviewer finding on #281. Fixing only the order he happened to report
    would have left him hitting the same split from the other side.
    """
    assert clean_capture_text(IMG + "\n\ntesting image upload") == [
        IMG + " testing image upload"
    ]


def test_two_images_attached_before_any_text_all_join_it():
    other = "![b.png](/api/upload/00112233445566778899aabbccddeeff.png)"
    assert clean_capture_text(IMG + "\n" + other + "\n\nboth of these") == [
        IMG + " " + other + " both of these"
    ]


def test_only_the_first_bullet_takes_the_images_he_attached_first():
    """A second thought typed after is still its own capture."""
    assert clean_capture_text(IMG + "\n\nfirst\n\nsecond") == [
        IMG + " first",
        "second",
    ]


def test_an_image_with_no_text_on_either_side_is_still_its_own_capture():
    """Nothing to attach it to, and dropping it would lose the picture."""
    assert clean_capture_text(IMG) == [IMG]


def test_text_typed_after_an_image_starts_a_new_capture():
    """Only the attachment folds backwards; a sentence is still a sentence."""
    assert clean_capture_text("first\n\n" + IMG + "\n\nsecond") == [
        "first " + IMG,
        "second",
    ]


@pytest.mark.parametrize(
    "typed",
    [
        "![local](/img/cat.png)",
        "![remote](https://example.com/api/upload/x.jpg)",
        "see the shot: " + IMG,
        "![half](/api/upload/x.jpg",
    ],
)
def test_only_a_line_this_site_wrote_folds_backwards(typed):
    """The exception is narrow on purpose: anything he typed is a capture.

    The third case is the one that matters most -- an attachment already
    sharing a line with text is not a line of its own, and folding it
    would silently merge two things he wrote separately.
    """
    assert clean_capture_text("first thing\n" + typed) == ["first thing", typed]


# --- where the bullet lands -----------------------------------------------


@pytest.mark.parametrize("fixture", ["issues_capture_sample.md", "ideas_capture_sample.md"])
def test_a_capture_lands_in_the_list_above_the_first_heading(fixture):
    """Both real shapes, including the one with no blank line after the
    frontmatter -- the case an offset-based insertion gets wrong."""
    out = insert_captures(_fixture(fixture), ["voice messages still fail"])
    assert _capture_list(out) == ["- voice messages still fail", "- "]
    assert "## Board" in out


@pytest.mark.parametrize("fixture", ["issues_capture_sample.md", "ideas_capture_sample.md"])
def test_the_frontmatter_is_untouched(fixture):
    original = _fixture(fixture)
    out = insert_captures(original, ["something"])
    assert out.split("---")[1] == original.split("---")[1]


@pytest.mark.parametrize("fixture", ["issues_capture_sample.md", "ideas_capture_sample.md"])
def test_a_capture_changes_exactly_one_line_of_the_file(fixture):
    """Caught by a dry run against the live 35KB files, not by the tests
    above: the first version filtered blank lines out of the capture list
    region, which deleted the blank line `issues.md` keeps between its
    frontmatter and its bullet -- `ideas.md` has none. Both files rendered
    the same afterwards, so nothing would have failed; it was simply me
    reformatting a file that is his. Asserting on the diff rather than on
    the bullets is what makes that visible.
    """
    original = _fixture(fixture)
    out = insert_captures(original, ["one new thought"])
    changed = [
        line for line in difflib.unified_diff(
            original.split("\n"), out.split("\n"), lineterm="", n=0)
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert changed == ["+- one new thought"]


def test_exactly_one_empty_bullet_survives_and_it_is_last(issues_md):
    """The contract in the file's own frontmatter: one empty bullet, always
    there, so he can start typing immediately."""
    out = insert_captures(issues_md, ["one"])
    out = insert_captures(out, ["two"])
    out = insert_captures(out, ["three"])
    assert _capture_list(out) == ["- one", "- two", "- three", "- "]


def test_captures_accumulate_in_the_order_they_were_written(ideas_md):
    out = insert_captures(ideas_md, ["first", "second"])
    assert _capture_list(out) == ["- first", "- second", "- "]


def test_an_existing_unprocessed_capture_is_never_lost(issues_md):
    """His unprocessed bullets are the strongest signal a cycle gets; a
    capture that overwrote one would be worse than no capture box."""
    with_his = insert_captures(issues_md, ["his own note"])
    out = insert_captures(with_his, ["mine"])
    assert _capture_list(out) == ["- his own note", "- mine", "- "]


def test_nothing_below_the_first_heading_is_touched(issues_md):
    original_tail = issues_md.split("## Board", 1)[1]
    out = insert_captures(issues_md, ["something"])
    assert out.split("## Board", 1)[1] == original_tail


def test_a_bullet_in_the_board_is_not_mistaken_for_the_capture_list():
    """The scan stops at the heading, so a list further down the file
    cannot capture the write."""
    markdown = "---\nx: 1\n---\n\n- \n\n## Board\n\n- not the capture list\n"
    out = insert_captures(markdown, ["mine"])
    assert _capture_list(out) == ["- mine", "- "]
    assert out.endswith("## Board\n\n- not the capture list\n")


def test_a_file_that_lost_its_empty_bullet_gets_the_list_restored():
    markdown = "---\nx: 1\n---\n\n## Board\n\nrows\n"
    out = insert_captures(markdown, ["mine"])
    assert _capture_list(out) == ["- mine", "- "]
    assert "## Board" in out


def test_a_file_with_no_frontmatter_still_captures():
    out = insert_captures("- \n\n## Board\n", ["mine"])
    assert _capture_list(out) == ["- mine", "- "]


def test_text_that_looks_like_markdown_stays_inside_its_bullet(issues_md):
    """A capture cannot restructure the file it lands in: whatever is typed
    is one bullet's text, so a `##` in it is content, not a new heading."""
    out = insert_captures(issues_md, ["## Board is broken"])
    assert "- ## Board is broken" in out
    assert _capture_list(out) == ["- ## Board is broken", "- "]


def test_no_bullets_leaves_the_file_byte_identical(issues_md):
    assert insert_captures(issues_md, []) == issues_md


# --- the write ------------------------------------------------------------


def test_capture_writes_the_updated_file_to_the_right_path(issues_md):
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(issues_md, "1-x")), \
            patch.object(nova_capture, "vault_write_path", return_value="written") as write:
        ok, message = capture("issues", "the app needs a restart")
    assert ok, message
    path, content = write.call_args[0]
    assert path == CAPTURE_TARGETS["issues"]
    assert "- the app needs a restart" in content


def test_an_unknown_target_never_reaches_the_vault():
    """`target` indexes a fixed dict; no path from a client is ever used."""
    with patch.object(nova_capture, "vault_write_path") as write:
        ok, message = capture("../../etc/passwd", "x")
    assert not ok
    assert "unknown target" in message
    write.assert_not_called()


def test_an_empty_capture_never_reaches_the_vault():
    with patch.object(nova_capture, "vault_write_path") as write:
        ok, message = capture("ideas", "   \n  ")
    assert not ok
    assert "nothing to capture" in message
    write.assert_not_called()


def test_a_conflicting_write_is_retried_against_freshly_read_content(issues_md):
    """CouchDB rejects a stale `_rev` with 409, which is the design working:
    someone else wrote between the read and the PUT. Resending the same body
    would clobber them, so the retry re-reads and rebuilds on top of what
    landed."""
    his_note = insert_captures(issues_md, ["something he typed meanwhile"])
    reads = [issues_md, his_note]
    with patch.object(nova_capture, "vault_read_path_rev",
                       side_effect=[(c, "1-x") for c in reads]) as read, \
            patch.object(nova_capture, "vault_write_path",
                         side_effect=["FAILED(409)", "written"]) as write:
        ok, message = capture("issues", "mine")
    assert ok, message
    assert read.call_count == 2
    final = write.call_args[0][1]
    assert _capture_list(final) == ["- something he typed meanwhile", "- mine", "- "]


def test_a_write_that_keeps_conflicting_gives_up_rather_than_spinning(issues_md):
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(issues_md, "1-x")), \
            patch.object(nova_capture, "vault_write_path", return_value="FAILED(409)") as write:
        ok, _ = capture("issues", "mine")
    assert not ok
    assert write.call_count == nova_capture.WRITE_ATTEMPTS


def test_a_non_conflict_failure_is_not_retried(issues_md):
    """A 401 or a 500 will fail identically next time; retrying it just
    triples the damage and the latency."""
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(issues_md, "1-x")), \
            patch.object(nova_capture, "vault_write_path", return_value="FAILED(401)") as write:
        ok, message = capture("issues", "mine")
    assert not ok
    assert "401" in message
    assert write.call_count == 1


def test_a_missing_target_file_is_reported_not_created():
    """`vault_write_path` would happily create it; a capture file that
    appeared from nowhere would be a silent second copy of the backlog."""
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(None, None)), \
            patch.object(nova_capture, "vault_write_path") as write:
        ok, message = capture("issues", "mine")
    assert not ok
    assert "not found" in message
    write.assert_not_called()


# --- notes: the third target (the owner, issues.md 2026-08-12) ---------------
#
# *"I should be able to just leave you notes instead of just issues and
# ideas. I have said this 2-3 times before. Add a button next to
# issues/ideas in the Nova app that lets me just send you notes."*
#
# `notes.md` is a real third shape rather than a copy: it has a `## Read`
# heading directly under the capture list where the other two have
# `## Board`, and it is the only one of the three with no `# Details`
# section at all. The fixture is the live file as created on 2026-08-12.


@pytest.fixture
def notes_md():
    return _fixture("notes_capture_sample.md")


def test_notes_is_a_capture_target_pointing_at_edvards_own_folder():
    """The folder changed on 2026-08-12; the database must not have.

    This used to assert `"/nova/" not in path`, which was a proxy for
    "routes to the owner's database" and stopped being one the moment his
    files moved into `projects/sokrates/projects/nova/`. The real rule is
    `vault.db_for`, so `test_vault_database_routing` asks it directly for
    every target; this one just pins the path.
    """
    assert CAPTURE_TARGETS["notes"] == "projects/sokrates/projects/nova/notes.md"
    assert CAPTURE_TARGETS["issues"] == "projects/sokrates/projects/nova/issues.md"
    assert CAPTURE_TARGETS["ideas"] == "projects/sokrates/projects/nova/ideas.md"
    assert not any(
        p.startswith("projects/sokrates/projects/agora/")
        for p in CAPTURE_TARGETS.values()
    )


def test_a_note_lands_above_the_read_heading(notes_md):
    """The list is found structurally, so a file whose first heading is
    `## Read` rather than `## Board` needs no special case -- but nothing
    was pinning that, and a note filed *below* `## Read` reads as already
    handled to every cycle after it."""
    out = insert_captures(notes_md, ["the vault sync is stuck again"])
    # Split on the heading at the start of a line: the frontmatter's
    # contract sentence names `## Read` inline, and splitting on the bare
    # string puts the whole file below a "heading" inside the frontmatter.
    above, _, below = out.partition("\n## Read")
    assert "- the vault sync is stuck again" in above
    assert "- the vault sync is stuck again" not in below


def test_a_note_keeps_the_single_empty_bullet_last(notes_md):
    out = insert_captures(notes_md, ["one", "two"])
    assert _capture_list(out) == ["- one", "- two", "- "]


def test_capture_writes_a_note_to_notes_md(notes_md):
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(notes_md, "1-x")), \
            patch.object(nova_capture, "vault_write_path", return_value="written") as write:
        ok, message = capture("notes", "you were right about the OOM kills")
    assert ok, message
    path, content = write.call_args[0]
    assert path == CAPTURE_TARGETS["notes"]
    assert "- you were right about the OOM kills" in content


def test_every_button_in_the_page_names_a_real_target():
    """The one coupling that can break silently.

    `app.js` sends whatever `data-target` a button carries and never names
    a target itself, so a button and this dict disagreeing is a capture
    that 400s on a phone with no sign of it here. Read out of the shipped
    HTML rather than a list, because a list would just be a fourth place
    to forget.
    """
    page = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "agora_runner", "nova_public", "index.html",
    )
    with open(page, encoding="utf-8") as handle:
        html = handle.read()
    buttons = re.findall(r'class="capture-btn" data-target="([^"]+)"', html)
    assert sorted(buttons) == sorted(CAPTURE_TARGETS)


# --- editing and deleting a capture (issues.md #66) -----------------------


def test_the_list_edvard_can_edit_is_the_one_he_typed_in(issues_md):
    """`list_captures` and the board page have to agree about what a
    capture is, or the page offers an Edit button on something the writer
    cannot find."""
    out = insert_captures(issues_md, ["first", "second"])
    assert list_captures(out)[-2:] == ["first", "second"]
    assert "" not in list_captures(out), "the cursor bullet is not a capture"


def test_a_bullet_below_the_first_heading_is_not_editable():
    """Same boundary as the insert: an edit must never be able to address
    a Board row."""
    markdown = "---\nx: 1\n---\n\n- mine\n- \n\n## Board\n\n- not the capture list\n"
    assert list_captures(markdown) == ["mine"]
    assert replace_capture(markdown, 1, "not the capture list", ["hacked"]) is None


def test_the_writer_and_the_page_agree_on_a_capture_that_wrapped():
    """The bug review found, and the reason `capture_entries` joins.

    The board joins a continuation into the bullet above it so the owner is
    never shown half a sentence -- and the page is what supplies the
    address an edit comes back with. A writer that did not join would find
    no single line matching that address, so Edit and Delete were dead on
    every wrapped capture while reporting "no longer in the list", which
    is false rather than merely unhelpful."""
    markdown = ("---\nx: 1\n---\n\n- the first half of what he typed\n"
                "  and the second half, wrapped.\n- \n\n## Board\n")
    joined = "the first half of what he typed and the second half, wrapped."
    assert _captures(markdown) == [joined], "the page's own parser moved"
    assert list_captures(markdown) == [joined]

    edited = replace_capture(markdown, 0, joined, ["one line now"])
    assert list_captures(edited) == ["one line now"]
    assert "wrapped." not in edited, "the continuation line was orphaned"


def test_two_captures_reading_the_same_are_told_apart_by_position():
    """The second bug review found. Matching on text alone rewrites
    whichever came first and reports success -- the wrong-line edit this
    design exists to prevent, dressed as a working feature."""
    markdown = "---\nx: 1\n---\n\n- fix this\n- fix this\n- \n\n## Board\n"
    edited = replace_capture(markdown, 1, "fix this", ["fixed the second one"])
    assert list_captures(edited) == ["fix this", "fixed the second one"]


def test_an_index_that_no_longer_holds_that_text_is_refused():
    """Both halves of the address have to agree. A cycle boarded the
    bullet above, so position 1 is now a different sentence."""
    markdown = "---\nx: 1\n---\n\n- second thing\n- \n\n## Board\n"
    assert replace_capture(markdown, 1, "the one he tapped", ["x"]) is None
    assert replace_capture(markdown, 0, "the one he tapped", ["x"]) is None


@pytest.mark.parametrize("index", [-1, 5, None, True, "0"])
def test_an_index_off_the_end_is_refused_rather_than_wrapped(index, issues_md):
    out = insert_captures(issues_md, ["only one"])
    assert replace_capture(out, index, "only one", ["x"]) is None


def test_an_edit_replaces_only_the_bullet_that_matches(issues_md):
    out = insert_captures(issues_md, ["first", "second", "third"])
    edited = replace_capture(out, list_captures(out).index("second"), "second", ["second, reworded"])
    assert list_captures(edited)[-3:] == ["first", "second, reworded", "third"]
    assert edited.split("## Board", 1)[1] == issues_md.split("## Board", 1)[1]


def test_a_delete_removes_the_bullet_and_leaves_the_cursor(issues_md):
    out = insert_captures(issues_md, ["first", "second"])
    deleted = replace_capture(out, list_captures(out).index("first"), "first", [])
    assert list_captures(deleted)[-1:] == ["second"]
    assert "first" not in list_captures(deleted)
    assert _capture_list(deleted)[-1] == "- ", "the empty cursor bullet was taken with it"


def test_an_edit_can_split_one_capture_into_several(issues_md):
    """The same rule as the box: one line per item, because a bullet
    holding a newline would break the list it lives in."""
    out = insert_captures(issues_md, ["two things at once"])
    edited = replace_capture(
        out, list_captures(out).index("two things at once"),
        "two things at once", clean_capture_text("one\ntwo"))
    assert list_captures(edited)[-2:] == ["one", "two"]


def test_a_capture_that_is_no_longer_there_is_not_an_edit(issues_md):
    """The case this design exists for: a cycle boarded the bullet while
    the page was open. Matching by text means the request finds nothing
    rather than editing whichever line now sits at that index."""
    assert replace_capture(issues_md, 0, "something a cycle already boarded", ["x"]) is None


def test_amend_reports_a_boarded_capture_without_writing(issues_md):
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(issues_md, "1-x")), \
            patch.object(nova_capture, "vault_write_path") as write:
        ok, message = amend("issues", 0, "not in the file", "new text")
    assert not ok
    assert "no longer" in message
    write.assert_not_called()


def test_amend_writes_the_edited_file_to_the_right_path(issues_md):
    start = insert_captures(issues_md, ["the app needs a restart"])
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(start, "1-x")), \
            patch.object(nova_capture, "vault_write_path", return_value="written") as write:
        ok, message = amend("issues", 0, "the app needs a restart", "the app needs two restarts")
    assert ok, message
    path, content = write.call_args[0]
    assert path == CAPTURE_TARGETS["issues"]
    assert list_captures(content)[-1] == "the app needs two restarts"


def test_amend_with_no_text_deletes(issues_md):
    start = insert_captures(issues_md, ["a typo I want gone"])
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(start, "1-x")), \
            patch.object(nova_capture, "vault_write_path", return_value="written") as write:
        ok, message = amend("issues", 0, "a typo I want gone", "")
    assert ok, message
    assert "deleted" in message
    assert "a typo I want gone" not in list_captures(write.call_args[0][1])


def test_an_unknown_target_never_reaches_the_vault_from_an_amend():
    with patch.object(nova_capture, "vault_write_path") as write:
        ok, message = amend("../../etc/passwd", 0, "x", "y")
    assert not ok
    assert "unknown target" in message
    write.assert_not_called()


def test_an_amend_conflict_is_retried_against_freshly_read_content(issues_md):
    """Exactly the capture box's 409 retry, and it matters more here: the
    concurrent writer is a cycle boarding this very file."""
    start = insert_captures(issues_md, ["mine"])
    meanwhile = insert_captures(start, ["something he typed on the phone"])
    with patch.object(nova_capture, "vault_read_path_rev",
                       side_effect=[(c, "1-x") for c in [start, meanwhile]]) as read, \
            patch.object(nova_capture, "vault_write_path",
                         side_effect=["FAILED(409)", "written"]) as write:
        ok, message = amend("issues", 0, "mine", "mine, reworded")
    assert ok, message
    assert read.call_count == 2
    final = write.call_args[0][1]
    assert "mine, reworded" in list_captures(final)
    assert "something he typed on the phone" in list_captures(final), \
        "the retry resent a stale body and clobbered his write"


def test_an_amend_conflict_that_loses_to_a_boarding_does_not_resurrect_it(issues_md):
    """The retry re-reads, and the re-read no longer has the bullet: a
    cycle boarded it between the attempts. Rebuilding on top would put it
    back in the list the owner just watched it leave."""
    start = insert_captures(issues_md, ["mine"])
    with patch.object(nova_capture, "vault_read_path_rev",
                       side_effect=[(c, "1-x") for c in [start, issues_md]]), \
            patch.object(nova_capture, "vault_write_path",
                         side_effect=["FAILED(409)", "written"]) as write:
        ok, message = amend("issues", 0, "mine", "mine, reworded")
    assert not ok
    assert "no longer" in message
    assert write.call_count == 1


# --- The capture box against a CouchDB that enforces revisions ------------
#
# The three conflict tests above this block hand `vault_write_path` the
# string "FAILED(409)" and watch what `capture` does with it. That proves
# the retry branches on 409. It does not prove a 409 can ever reach it.
#
# Measured Cycle 142: delete `if_rev=rev` from either write site in
# `nova_capture.py` and all 123 tests in this file and `test_nova_comments`
# still pass -- while the capture box goes back to silently overwriting
# whoever it raced, which is the exact bug Cycle 138 shipped. The tests
# below run the real client against `FakeCouch`, which applies CouchDB's
# actual rule, so dropping the revision fails them.


def _seeded(path, content):
    couch = FakeCouch()
    couch.seed(path, content)
    return couch


def test_a_capture_that_loses_a_real_race_keeps_the_writer_that_won(issues_md):
    """The end-to-end version of the retry test above.

    `interleave={2: ...}` lands the other writer between `capture`'s own
    read and the lookup inside `vault_write_path` -- the only window where
    an unconditional write silently adopts the winner's revision and
    overwrites them. Interleaving anywhere later is caught either way and
    proves nothing.
    """
    path = CAPTURE_TARGETS["issues"]
    couch = _seeded(path, issues_md)
    couch.interleave = {2: lambda c: c.seed(
        path, insert_captures(issues_md, ["something he typed meanwhile"]))}
    with patch.object(vault, "couch_req", couch.req):
        ok, message = capture("issues", "mine")
    assert ok, message
    assert couch.rejected == 1, "the losing write must have been refused"
    assert _capture_list(couch.text(path)) == [
        "- something he typed meanwhile", "- mine", "- "]


def test_an_amend_that_loses_a_real_race_keeps_the_writer_that_won(issues_md):
    """Same window, the edit/delete path, and the damage is worse here:
    `capture` only inserts a bullet, `amend` rewrites the whole file, so an
    unconditional write drops everything the other writer did.

    The interloper edits the board rather than the capture list on purpose.
    A new bullet above the amended one moves it, and `amend` then correctly
    refuses (the position and the text disagree -- Cycle 132's design). The
    losing-and-retrying case only exists when the rest of the file moved,
    which is what a cycle boarding an item actually does.
    """
    path = CAPTURE_TARGETS["issues"]
    start = insert_captures(issues_md, ["mine"])
    his_board = start.replace(
        "|---|------|--------|---------|",
        "|---|------|--------|---------|\n| 70 | boarded meanwhile | Open | 2026-08-12 |")
    assert his_board != start
    couch = _seeded(path, start)
    couch.interleave = {2: lambda c: c.seed(path, his_board)}
    with patch.object(vault, "couch_req", couch.req):
        ok, message = amend("issues", 0, "mine", "mine, edited")
    assert ok, message
    assert couch.rejected == 1, "the losing write must have been refused"
    final = couch.text(path)
    assert _capture_list(final) == ["- mine, edited", "- "]
    assert "| 70 | boarded meanwhile | Open | 2026-08-12 |" in final, \
        "the board row the other writer added must survive the amend"


# --- moving a capture between the three files -----------------------------
#
# The owner, 2026-08-24: *"The note i sent regarding the rebuilding the
# notes page was sent as a note, but its actually an idea, but i have no
# way of changing it or editing it. So we need crude operations for notes,
# but also the possibility to change issues/ideas/notes into one of the
# other."*


def _two_file_vault(paths):
    """A FakeCouch seeded with several capture files at once."""
    couch = FakeCouch()
    for target, markdown in paths.items():
        couch.seed(CAPTURE_TARGETS[target], markdown)
    return couch


def test_convert_moves_the_bullet_out_of_one_file_and_into_the_other(notes_md, ideas_md):
    couch = _two_file_vault({"notes": insert_captures(notes_md, ["rebuild the notes page"]),
                             "ideas": ideas_md})
    with patch.object(vault, "couch_req", couch.req):
        ok, message = convert_capture("notes", 0, "rebuild the notes page", "ideas")
    assert ok, message
    assert "rebuild the notes page" not in couch.text(CAPTURE_TARGETS["notes"])
    assert "- rebuild the notes page" in couch.text(CAPTURE_TARGETS["ideas"])


def test_convert_leaves_the_source_alone_when_the_destination_write_fails(notes_md):
    """Write-then-delete, and this is the half it buys.

    The destination is not seeded, so `vault_read_path_rev` returns None
    and `capture` refuses before anything is removed. His sentence has to
    still be in `notes.md` afterwards -- that is the whole reason the two
    calls are in this order.
    """
    couch = _two_file_vault({"notes": insert_captures(notes_md, ["actually an idea"])})
    with patch.object(vault, "couch_req", couch.req):
        ok, message = convert_capture("notes", 0, "actually an idea", "ideas")
    assert not ok
    assert "actually an idea" in couch.text(CAPTURE_TARGETS["notes"]), \
        "a failed destination write must never cost him the line"


def test_convert_says_so_when_the_copy_landed_but_the_removal_did_not(notes_md, ideas_md):
    """The one half-done state this ordering can produce, reported not hidden."""
    couch = _two_file_vault({"notes": insert_captures(notes_md, ["actually an idea"]),
                             "ideas": ideas_md})
    with patch.object(vault, "couch_req", couch.req), \
            patch.object(nova_capture, "amend", return_value=(False, "boom")):
        ok, message = convert_capture("notes", 0, "actually an idea", "ideas")
    assert not ok
    assert "it is in both" in message, message
    assert "- actually an idea" in couch.text(CAPTURE_TARGETS["ideas"])


def test_convert_carries_the_rating_between_the_two_boards(issues_md, ideas_md):
    rated = "🟠 High: the runner drops replies"
    couch = _two_file_vault({"issues": insert_captures(issues_md, [rated]),
                             "ideas": ideas_md})
    with patch.object(vault, "couch_req", couch.req):
        ok, message = convert_capture("issues", 0, rated, "ideas")
    assert ok, message
    assert "- " + rated in couch.text(CAPTURE_TARGETS["ideas"])


def test_convert_strips_the_rating_going_into_notes(issues_md, notes_md):
    """`notes.md` is *"never numbered, never boarded"* -- a priority label
    in a file with no board is vocabulary from a page that does not exist."""
    rated = "🟠 High: the runner drops replies"
    couch = _two_file_vault({"issues": insert_captures(issues_md, [rated]),
                             "notes": notes_md})
    with patch.object(vault, "couch_req", couch.req):
        ok, message = convert_capture("issues", 0, rated, "notes")
    assert ok, message
    notes = couch.text(CAPTURE_TARGETS["notes"])
    assert "- the runner drops replies" in notes
    assert "🟠" not in notes


def test_convert_refuses_a_stale_address_without_moving_anything(notes_md, ideas_md):
    """The bullet the page addressed is not the bullet in the file.

    `replace_capture` refuses rather than resolving, and the destination
    copy is what the message tells him to delete.
    """
    couch = _two_file_vault({"notes": insert_captures(notes_md, ["one"]), "ideas": ideas_md})
    with patch.object(vault, "couch_req", couch.req):
        ok, message = convert_capture("notes", 0, "something else entirely", "ideas")
    assert not ok
    assert "- one" in couch.text(CAPTURE_TARGETS["notes"])


def test_convert_refuses_unknown_and_identical_targets():
    with patch.object(nova_capture, "vault_read_path_rev") as read:
        assert convert_capture("notes", 0, "x", "notes")[0] is False
        assert convert_capture("notes", 0, "x", "kanban")[0] is False
        assert convert_capture("kanban", 0, "x", "ideas")[0] is False
        assert convert_capture("notes", 0, "   ", "ideas")[0] is False
    assert read.call_count == 0, "a refused conversion must not touch the vault"


def test_a_second_convert_of_the_same_line_does_not_claim_it_is_in_both(notes_md, ideas_md):
    """The double-tap, which is what the page's disabled buttons now prevent
    and what the message has to be honest about if it happens anyway.

    The first call moved the line. The second finds the destination write
    succeeds again -- it is unconditional -- and the removal refuses because
    the address is stale. The source is *clean* at that point, so "it is in
    both, delete the notes one" would send him to the wrong file for a copy
    that is not there. Found by review.
    """
    couch = _two_file_vault({"notes": insert_captures(notes_md, ["actually an idea"]),
                             "ideas": ideas_md})
    with patch.object(vault, "couch_req", couch.req):
        assert convert_capture("notes", 0, "actually an idea", "ideas")[0] is True
        ok, message = convert_capture("notes", 0, "actually an idea", "ideas")
    assert not ok
    assert "it is in both" not in message, message
    assert "duplicate" in message, message
    assert "actually an idea" not in couch.text(CAPTURE_TARGETS["notes"]), \
        "the source really is clean, so the message must not point him at it"
    assert couch.text(CAPTURE_TARGETS["ideas"]).count("- actually an idea") == 2, \
        "the fixture must actually have produced the duplicate this is about"


# --- answering a capture in place -----------------------------------------


def test_a_reply_lands_as_an_indented_bullet_under_his_capture(issues_md):
    """The write half of the top of the ranking.

    `top_board_rows` puts his bare bullets above every boarded row, and
    until this there was no route that could answer one -- the comment API
    is keyed by a row number and a capture has none. Six handoffs in a row
    filed it.
    """
    markdown = insert_captures(issues_md, ["the thing he typed"])
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(markdown, "1-x")), \
            patch.object(nova_capture, "vault_write_path", return_value="written") as write:
        ok, message = comment_on_capture("issues", 0, "the thing he typed", "Answered, cycle 430.")
    assert ok, message
    path, content = write.call_args[0]
    assert path == CAPTURE_TARGETS["issues"]
    assert "- the thing he typed\n  - Answered, cycle 430." in content
    # And the reply is not a second capture: his list still reads the same.
    assert list_captures(content) == list_captures(markdown)


def test_a_second_reply_goes_under_the_first(issues_md):
    markdown = insert_captures(issues_md, ["his line"])
    once = reply_under_capture(markdown, 0, "his line", "first answer")
    twice = reply_under_capture(once, 0, "his line", "second answer")
    assert "- his line\n  - first answer\n  - second answer" in twice


def test_the_board_page_address_answers_the_same_capture(issues_md):
    """Two pages, two spellings of the same bullet, and both must resolve.

    The notes page draws a reply as its own bubble and sends his sentence
    alone; the board page folds the reply into the capture card and sends
    the two joined. A route that took only one of them would have a dead
    button on the other page.
    """
    markdown = insert_captures(issues_md, ["his line"])
    once = reply_under_capture(markdown, 0, "his line", "first answer")
    joined = _captures(once)[0]
    assert joined == "his line first answer", "the board page's parser moved"
    assert reply_under_capture(once, 0, joined, "second answer") is not None


def test_a_reply_to_a_capture_that_moved_is_refused(issues_md):
    """A cycle boarded it while the reply was being written: no write."""
    markdown = insert_captures(issues_md, ["his line"])
    with patch.object(nova_capture, "vault_read_path_rev", return_value=(markdown, "1-x")), \
            patch.object(nova_capture, "vault_write_path") as write:
        ok, message = comment_on_capture("issues", 0, "something he never typed", "hi")
    assert not ok
    assert STALE_CAPTURE in message
    write.assert_not_called()


def test_a_reply_with_a_line_break_never_reaches_the_vault(issues_md):
    """One indented bullet. A break in it splits into a bullet and a stray
    paragraph, which the next parser reads as a continuation of something
    else -- the same rule `comment_on_row` has for the same reason."""
    with patch.object(nova_capture, "vault_write_path") as write:
        ok, message = comment_on_capture("issues", 0, "his line", "one\ntwo")
    assert not ok
    assert "line break" in message
    write.assert_not_called()


def test_an_unknown_target_never_reaches_the_vault_on_a_reply():
    with patch.object(nova_capture, "vault_write_path") as write:
        ok, message = comment_on_capture("../../etc/passwd", 0, "x", "y")
    assert not ok
    assert "unknown target" in message
    write.assert_not_called()


def test_editing_a_capture_keeps_the_reply_and_deleting_it_does_not(issues_md):
    """His edit is a rewording of his own sentence; a cycle's answer under
    it is not his to lose. A delete is the other way round -- an answer to
    a bullet that is gone is orphaned text in his file."""
    markdown = insert_captures(issues_md, ["his line"])
    answered = reply_under_capture(markdown, 0, "his line", "my answer")
    edited = replace_capture(answered, 0, "his line", ["his line, reworded"])
    assert "- his line, reworded\n  - my answer" in edited
    deleted = replace_capture(answered, 0, "his line", [])
    assert "my answer" not in deleted
