"""Did a silent cycle's Claude Code session ever start? (`tools.cli_sessions`)"""

import datetime

import pytest

from tools import cli_sessions
from tools.cli_sessions import (
    apply_cli_sessions,
    index,
    sessions_near,
)


UTC = datetime.timezone.utc


def at(hour, minute=0, day=6):
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def rows(mapping):
    """A `read` stand-in: `{path: first-row dict}`."""
    return lambda path: mapping.get(path)


def heartbeat(stamp):
    return {"timestamp": stamp, "type": "queue-operation",
            "content": "[Automatic heartbeat trigger — address Edvard directly.]"}


def chat(stamp, text="Please revert this."):
    return {"timestamp": stamp, "type": "queue-operation", "content": text}


class TestIndex:
    def test_a_chat_session_is_not_a_heartbeat_session(self):
        """The live false positive: cycle 1082's window holds a chat session.

        A timing join alone would report that cycle's CLI as having run.
        """
        sessions, _ = index(paths=["hb.jsonl", "chat.jsonl"],
                            read=rows({"hb.jsonl": heartbeat("2026-09-06T06:30:00Z"),
                                       "chat.jsonl": chat("2026-09-06T06:31:00Z")}))
        assert [path for _, path in sessions] == ["hb.jsonl"]

    def test_reach_is_measured_over_every_transcript_not_only_heartbeats(self):
        """Otherwise a chat-only stretch reads as "before the archive exists"."""
        sessions, reach = index(
            paths=["chat.jsonl", "hb.jsonl"],
            read=rows({"chat.jsonl": chat("2026-09-01T00:00:00Z"),
                       "hb.jsonl": heartbeat("2026-09-06T06:30:00Z")}))
        assert reach == datetime.datetime(2026, 9, 1, tzinfo=UTC)
        assert [path for _, path in sessions] == ["hb.jsonl"]

    def test_a_transcript_with_no_readable_stamp_is_skipped_not_counted(self):
        sessions, reach = index(paths=["bad.jsonl", "hb.jsonl"],
                                read=rows({"bad.jsonl": {"timestamp": "not a date",
                                                         "content": "[Automatic heartbeat trigger]"},
                                           "hb.jsonl": heartbeat("2026-09-06T06:30:00Z")}))
        assert [path for _, path in sessions] == ["hb.jsonl"]
        assert reach == at(6, 30)

    def test_an_empty_archive_reaches_nowhere(self):
        sessions, reach = index(paths=[], read=rows({}))
        assert sessions == []
        assert reach is None

    def test_sessions_come_back_oldest_first(self):
        sessions, _ = index(paths=["b.jsonl", "a.jsonl"],
                            read=rows({"b.jsonl": heartbeat("2026-09-06T09:00:00Z"),
                                       "a.jsonl": heartbeat("2026-09-06T06:00:00Z")}))
        assert [path for _, path in sessions] == ["a.jsonl", "b.jsonl"]


class TestSessionsNear:
    def test_the_window_is_asymmetric_because_the_session_starts_after(self):
        sessions = [(at(6, 27), "early.jsonl"),
                    (at(6, 31), "mine.jsonl"),
                    (at(6, 39), "late.jsonl")]
        assert sessions_near(sessions, at(6, 30)) == ["mine.jsonl"]

    def test_the_edges_are_inclusive(self):
        sessions = [(at(6, 28), "lead.jsonl"), (at(6, 38), "trail.jsonl")]
        assert sessions_near(sessions, at(6, 30)) == ["lead.jsonl", "trail.jsonl"]


class TestApply:
    def silent(self, number=1029):
        return [{"number": number, "verdict": "silent"}]

    def test_no_session_is_recorded_as_an_empty_list_not_as_a_miss(self):
        """`{"paths": []}` and "I could not look" must stay different facts."""
        results = self.silent()
        apply_cli_sessions(results, {1029: {"createdAt": "2026-09-06T04:30:00Z"}},
                           [(at(9, 0), "other.jsonl")], reach=at(0, 0))
        assert results[0]["cli_session"]["paths"] == []

    def test_a_session_in_the_window_is_named(self):
        results = self.silent()
        apply_cli_sessions(results, {1029: {"createdAt": "2026-09-06T04:30:00Z"}},
                           [(at(4, 31), "mine.jsonl")], reach=at(0, 0))
        assert results[0]["cli_session"]["paths"] == ["mine.jsonl"]

    def test_a_conversation_older_than_the_archive_is_uncovered(self):
        """Cycle 8 ran 2026-08-03; no transcript on this disk is that old."""
        results = self.silent(8)
        apply_cli_sessions(results, {8: {"createdAt": "2026-08-03T10:42:00Z"}},
                           [(at(4, 31), "mine.jsonl")], reach=at(0, 0))
        assert results[0]["cli_session"] == {"uncovered": at(0, 0),
                                             "created": datetime.datetime(
                                                 2026, 8, 3, 10, 42, tzinfo=UTC)}

    def test_an_empty_archive_is_uncovered_never_an_absent_session(self):
        results = self.silent()
        apply_cli_sessions(results, {1029: {"createdAt": "2026-09-06T04:30:00Z"}},
                           [], reach=None)
        assert "uncovered" in results[0]["cli_session"]

    def test_a_conversation_with_no_clock_says_so(self):
        results = self.silent()
        apply_cli_sessions(results, {1029: {}}, [], reach=at(0, 0))
        assert results[0]["cli_session"] == {"no_clock": True}

    def test_only_silent_rows_are_touched(self):
        results = [{"number": 359, "verdict": "lost"},
                   {"number": 1029, "verdict": "silent"}]
        apply_cli_sessions(results, {359: {"createdAt": "2026-09-06T04:30:00Z"},
                                     1029: {"createdAt": "2026-09-06T04:30:00Z"}},
                           [], reach=at(0, 0))
        assert "cli_session" not in results[0]
        assert "cli_session" in results[1]


class TestReport:
    """The line `cycle_postmortem` prints under a silent row."""

    def lines(self, life):
        from tools.cycle_postmortem import _cli_session_lines
        return "\n".join(_cli_session_lines(life))

    def test_an_absent_session_names_the_runner_not_only_the_absence(self):
        text = self.lines({"paths": [], "created": at(6, 30)})
        assert "no Claude Code session started" in text
        assert "never reached the bridge" in text

    def test_a_session_that_started_is_printed_with_its_path(self):
        text = self.lines({"paths": ["/x/a.jsonl"], "created": at(6, 30)})
        assert "/x/a.jsonl" in text
        assert "DID start" in text

    def test_uncovered_and_absent_do_not_read_the_same(self):
        absent = self.lines({"paths": [], "created": at(6, 30)})
        uncovered = self.lines({"uncovered": at(6, 0), "created": at(5, 0)})
        assert absent != uncovered
        assert "does not reach back" in uncovered

    def test_an_empty_archive_says_it_holds_nothing_placeable(self):
        text = self.lines({"uncovered": None, "created": at(6, 30)})
        assert "no transcript this can place on a clock" in text

    def test_no_join_prints_nothing(self):
        assert self.lines(None) == ""


class TestReportWiring:
    """The line has to reach `format_report`, not only exist as a formatter.

    Deleting the `lines.extend` call in the report left the whole suite
    green, because every test above called the formatter directly. This is
    the test that fails when the join is computed and never printed.
    """

    def row(self, session):
        return {"number": 1029, "verdict": "silent", "messages": 0,
                "recent": True, "detail": "the conversation was created and "
                "nothing ever spoke in it", "cli_session": session}

    def test_the_absent_session_line_reaches_the_printed_report(self):
        from tools.cycle_postmortem import format_report
        text, _ = format_report([self.row({"paths": [], "created": at(6, 30)})],
                                newest=1261, error=None)
        assert "no Claude Code session started" in text

    def test_the_named_session_line_reaches_the_printed_report(self):
        from tools.cycle_postmortem import format_report
        text, _ = format_report(
            [self.row({"paths": ["/x/a.jsonl"], "created": at(6, 30)})],
            newest=1261, error=None)
        assert "/x/a.jsonl" in text
