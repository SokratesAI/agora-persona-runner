"""Tests for tools.changelog_watch.

The ones that matter are the two ways this could report a clean sweep it
had not taken: a version the changelog does not carry (which looks
exactly like "nothing newer"), and a document whose section shape has
moved (which parses to nothing at all). Both have to exit 1.
"""

import io

import pytest

from tools import changelog_watch
from tools.changelog_watch import matching, newer_than, parse_sections


CHANGELOG = """# Changelog

## 2.1.247

- Added a thing nobody here uses
- Changed `--forward-subagent-text` to also carry tool results

## 2.1.246

- Fixed a wrapped bullet that keeps going
  onto a second line
- Fixed something unrelated

## 2.1.245

- Removed `messaging_socket_path`
"""


def fake_opener(body):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda request, timeout=None: Response(body.encode())


@pytest.fixture
def served(monkeypatch):
    def serve(text=CHANGELOG):
        monkeypatch.setattr(
            changelog_watch, "fetch_changelog",
            lambda opener=None: (text, None),
        )
    return serve


def test_parse_sections_reads_versions_and_bullets():
    sections = parse_sections(CHANGELOG)
    assert [version for version, _ in sections] == ["2.1.247", "2.1.246", "2.1.245"]
    assert sections[2][1] == ["Removed `messaging_socket_path`"]


def test_a_wrapped_bullet_is_joined_onto_one_line():
    """A term on a bullet's second line has to match the same entry."""
    entries = dict(parse_sections(CHANGELOG))["2.1.246"]
    assert entries[0] == "Fixed a wrapped bullet that keeps going onto a second line"


def test_newer_than_is_strict_and_ordered():
    sections = parse_sections(CHANGELOG)
    gap = newer_than(sections, "2.1.245")
    assert [version for version, _ in gap] == ["2.1.247", "2.1.246"]


def test_newer_than_is_none_for_a_version_the_changelog_does_not_carry():
    """The failure the exit contract is built around.

    An unknown version yields no newer sections, which is the same empty
    list a current version yields. They mean opposite things, so this
    returns None and main() exits 1 rather than reporting clean.
    """
    assert newer_than(parse_sections(CHANGELOG), "9.9.9") is None


def test_matching_is_case_sensitive_and_names_the_term_it_hit():
    hits, misses = matching(
        ["Changed `--verbose` output", "Made the docs less verbose"],
        ("--verbose",),
    )
    assert [entry for entry, _ in hits] == ["Changed `--verbose` output"]
    assert hits[0][1] == ["--verbose"]
    assert len(misses) == 1


def test_exit_2_when_the_gap_names_something_on_the_watch_list(served, capsys):
    served()
    assert changelog_watch.main(["--since", "2.1.245"]) == 2
    out = capsys.readouterr().out
    assert "TOUCHES US" in out
    assert "--forward-subagent-text" in out


def test_exit_0_when_the_gap_touches_nothing_we_depend_on(served, capsys):
    served()
    assert changelog_watch.main(["--since", "2.1.245", "--watch", "nothing-here"]) == 0
    assert "Nothing on the watch list" in capsys.readouterr().out


def test_exit_0_when_there_is_nothing_newer(served, capsys):
    served()
    assert changelog_watch.main(["--since", "2.1.247"]) == 0
    assert "Nothing newer" in capsys.readouterr().out


def test_exit_1_when_the_version_cannot_be_placed(served, capsys):
    served()
    assert changelog_watch.main(["--since", "9.9.9"]) == 1
    assert "COULD NOT PLACE" in capsys.readouterr().out


def test_exit_1_when_the_document_shape_has_moved(served, capsys):
    """No `## <version>` headings at all is a blind parser, not a clean sweep."""
    served("# Changelog\n\nRelease notes have moved to the website.\n")
    assert changelog_watch.main(["--since", "2.1.245"]) == 1
    assert "COULD NOT READ THE CHANGELOG" in capsys.readouterr().out


def test_exit_1_when_the_changelog_is_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(
        changelog_watch, "fetch_changelog",
        lambda opener=None: (None, "boom"),
    )
    assert changelog_watch.main(["--since", "2.1.245"]) == 1
    assert "COULD NOT READ THE CHANGELOG" in capsys.readouterr().out


def test_all_prints_the_entries_that_did_not_match(served, capsys):
    served()
    changelog_watch.main(["--since", "2.1.245", "--all"])
    assert "Added a thing nobody here uses" in capsys.readouterr().out


def test_fetch_changelog_reports_a_failure_rather_than_raising():
    def boom(request, timeout=None):
        raise OSError("no route")

    text, why = changelog_watch.fetch_changelog(opener=boom)
    assert text is None
    assert "no route" in why


def test_every_watch_term_is_a_non_empty_literal():
    """A blank or duplicated term would match every entry, or none."""
    assert len(set(changelog_watch.WATCH_TERMS)) == len(changelog_watch.WATCH_TERMS)
    assert all(term.strip() for term in changelog_watch.WATCH_TERMS)
