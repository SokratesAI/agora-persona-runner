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

import os
from unittest.mock import patch

import pytest

from agora_runner import nova_capture
from agora_runner.nova_capture import (
    CAPTURE_TARGETS,
    capture,
    clean_capture_text,
    insert_captures,
)

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
    """The bullets above the first heading -- the list Edvard writes in."""
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
    with patch.object(nova_capture, "vault_read_path", return_value=issues_md), \
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
    with patch.object(nova_capture, "vault_read_path", side_effect=reads) as read, \
            patch.object(nova_capture, "vault_write_path",
                         side_effect=["FAILED(409)", "written"]) as write:
        ok, message = capture("issues", "mine")
    assert ok, message
    assert read.call_count == 2
    final = write.call_args[0][1]
    assert _capture_list(final) == ["- something he typed meanwhile", "- mine", "- "]


def test_a_write_that_keeps_conflicting_gives_up_rather_than_spinning(issues_md):
    with patch.object(nova_capture, "vault_read_path", return_value=issues_md), \
            patch.object(nova_capture, "vault_write_path", return_value="FAILED(409)") as write:
        ok, _ = capture("issues", "mine")
    assert not ok
    assert write.call_count == nova_capture.WRITE_ATTEMPTS


def test_a_non_conflict_failure_is_not_retried(issues_md):
    """A 401 or a 500 will fail identically next time; retrying it just
    triples the damage and the latency."""
    with patch.object(nova_capture, "vault_read_path", return_value=issues_md), \
            patch.object(nova_capture, "vault_write_path", return_value="FAILED(401)") as write:
        ok, message = capture("issues", "mine")
    assert not ok
    assert "401" in message
    assert write.call_count == 1


def test_a_missing_target_file_is_reported_not_created():
    """`vault_write_path` would happily create it; a capture file that
    appeared from nowhere would be a silent second copy of the backlog."""
    with patch.object(nova_capture, "vault_read_path", return_value=None), \
            patch.object(nova_capture, "vault_write_path") as write:
        ok, message = capture("issues", "mine")
    assert not ok
    assert "not found" in message
    write.assert_not_called()
