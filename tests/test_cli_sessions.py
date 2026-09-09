"""Did a silent cycle's Claude Code session ever start? (`tools.cli_sessions`)"""

import json
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


class TestLastTurn:
    """How a session ended, which is what `paths` alone cannot say.

    Cycle 1262 read the two transcripts the join named. Cycle 360 ran 41
    minutes to a full reply Agora never carried; cycle 784 was killed 26
    seconds in, mid `Bash` call. Both print as "a session DID start", and
    the difference between them is the whole finding.
    """

    def write(self, tmp_path, rows):
        path = tmp_path / "s.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows))
        return str(path)

    def turn(self, at, blocks):
        return {"type": "assistant", "timestamp": at,
                "message": {"content": blocks}}

    def test_a_session_that_ends_on_prose_reports_that_prose(self, tmp_path):
        from tools.cli_sessions import last_turn
        path = self.write(tmp_path, [
            {"type": "user", "timestamp": "2026-08-24T08:44:00Z"},
            self.turn("2026-08-24T09:25:00Z", [{"type": "text",
                                                "text": "Edvard — done."}]),
        ])
        assert last_turn(path)["reply"] == "Edvard — done."

    def test_a_session_killed_mid_tool_call_reports_no_reply(self, tmp_path):
        from tools.cli_sessions import last_turn
        path = self.write(tmp_path, [
            {"type": "user", "timestamp": "2026-09-01T21:25:14Z"},
            self.turn("2026-09-01T21:25:40Z", [{"type": "tool_use",
                                                "name": "Bash", "input": {}}]),
        ])
        ending = last_turn(path)
        assert ending["reply"] is None
        assert ending["waiting_on"] == "Bash"
        assert ending["ran_for"] == 26

    def test_prose_before_a_later_tool_call_is_not_a_closing_reply(self, tmp_path):
        """The strictness is the point, not an implementation detail.

        A cycle narrates as it works. Taking the last `text` block anywhere
        in the file would report mid-cycle narration from a session that
        was then killed as a reply the owner never got --- which is the
        opposite verdict on the same transcript.
        """
        from tools.cli_sessions import last_turn
        path = self.write(tmp_path, [
            self.turn("2026-09-01T21:25:14Z", [{"type": "text",
                                                "text": "Now I will merge it."}]),
            self.turn("2026-09-01T21:25:40Z", [{"type": "tool_use",
                                                "name": "Bash", "input": {}}]),
        ])
        assert last_turn(path)["reply"] is None

    def test_prose_then_a_tool_use_in_one_turn_is_not_a_reply(self, tmp_path):
        from tools.cli_sessions import last_turn
        path = self.write(tmp_path, [
            self.turn("2026-09-01T21:25:14Z", [
                {"type": "text", "text": "Reading it now."},
                {"type": "tool_use", "name": "Read", "input": {}}]),
        ])
        assert last_turn(path)["reply"] is None

    def test_an_unknown_block_type_carrying_text_is_not_a_reply(self, tmp_path):
        """Why the type check is not redundant with the empty-text guard.

        Today every non-`text` block the CLI writes happens to lack a
        `text` key, so dropping the type check changes no verdict on any
        real transcript --- it survived the mutation round for exactly
        that reason. But that is a guarantee about somebody else's schema
        and I do not hold it: a block type added upstream that carries
        prose under `text` would start reading as a delivered reply on a
        session that never spoke.
        """
        from tools.cli_sessions import last_turn
        path = self.write(tmp_path, [
            self.turn("2026-09-01T21:25:14Z", [
                {"type": "server_tool_use", "text": "searching the web"}]),
        ])
        assert last_turn(path)["reply"] is None

    def test_an_empty_closing_text_block_is_not_a_reply(self, tmp_path):
        from tools.cli_sessions import last_turn
        path = self.write(tmp_path, [
            self.turn("2026-09-01T21:25:14Z", [{"type": "text", "text": "   "}]),
        ])
        assert last_turn(path)["reply"] is None

    def test_a_string_content_turn_still_reads_as_prose(self, tmp_path):
        from tools.cli_sessions import last_turn
        path = self.write(tmp_path, [
            {"type": "assistant", "timestamp": "2026-09-01T21:25:14Z",
             "message": {"content": "plain string"}},
        ])
        assert last_turn(path)["reply"] == "plain string"

    def test_an_unreadable_transcript_is_none_not_a_bad_ending(self, tmp_path):
        """Absent and killed are different facts, one layer further down."""
        from tools.cli_sessions import last_turn
        assert last_turn(str(tmp_path / "nope.jsonl")) is None

    def test_a_transcript_with_no_timestamp_is_none(self, tmp_path):
        from tools.cli_sessions import last_turn
        path = self.write(tmp_path, [{"type": "assistant",
                                      "message": {"content": "hi"}}])
        assert last_turn(path) is None


class TestApplySessionEndings:

    def rows(self, paths):
        return [{"number": 360, "verdict": "silent",
                 "cli_session": {"paths": list(paths)}}]

    def test_each_named_path_gets_its_own_ending(self):
        from tools.cli_sessions import apply_session_endings
        rows = self.rows(["/a.jsonl", "/b.jsonl"])
        apply_session_endings(rows, read=lambda p: {"ran_for": 1, "reply": p,
                                                    "waiting_on": None})
        assert rows[0]["cli_session"]["endings"]["/b.jsonl"]["reply"] == "/b.jsonl"

    def test_an_unreadable_path_is_absent_rather_than_shifting_the_rest(self):
        from tools.cli_sessions import apply_session_endings
        rows = self.rows(["/a.jsonl", "/b.jsonl"])
        apply_session_endings(
            rows, read=lambda p: None if p == "/a.jsonl"
            else {"ran_for": 1, "reply": "b", "waiting_on": None})
        endings = rows[0]["cli_session"]["endings"]
        assert list(endings) == ["/b.jsonl"]

    def test_a_row_that_is_not_silent_is_left_alone(self):
        from tools.cli_sessions import apply_session_endings
        rows = [{"number": 1, "verdict": "lost",
                 "cli_session": {"paths": ["/a.jsonl"]}}]
        apply_session_endings(rows, read=lambda p: {"ran_for": 1, "reply": "x",
                                                    "waiting_on": None})
        assert "endings" not in rows[0]["cli_session"]


class TestEndingLines:

    def lines(self, ending, path="/x/a.jsonl"):
        from tools.cycle_postmortem import _session_ending_lines
        return "\n".join(_session_ending_lines(path, ending))

    def test_a_closing_reply_prints_whole_because_it_is_the_only_copy(self):
        reply = "Line one.\n\nLine two, which is long enough to be worth keeping."
        text = self.lines({"ran_for": 2485, "reply": reply, "waiting_on": None})
        assert "41m 25s" in text
        assert "only the delivery was lost" in text
        for line in reply.splitlines():
            assert f"| {line}" in text

    def test_a_killed_session_names_the_tool_it_was_waiting_on(self):
        text = self.lines({"ran_for": 26, "reply": None, "waiting_on": "Bash"})
        assert "killed mid-turn after 26s waiting on `Bash`" in text
        assert "never reached a reply" in text

    def test_the_two_endings_do_not_read_the_same(self):
        finished = self.lines({"ran_for": 2485, "reply": "hi", "waiting_on": None})
        killed = self.lines({"ran_for": 26, "reply": None, "waiting_on": "Bash"})
        assert finished != killed
        assert "delivery" in finished and "delivery" not in killed

    def test_an_unreadable_transcript_says_so_rather_than_nothing(self):
        text = self.lines(None)
        assert "could not be read" in text

    def test_the_ending_reaches_the_printed_report(self):
        """The formatter existing is not the same as the report printing it."""
        from tools.cycle_postmortem import format_report
        row = {"number": 360, "verdict": "silent", "messages": 0, "recent": True,
               "detail": "the conversation was created and nothing ever spoke in it",
               "cli_session": {"paths": ["/x/a.jsonl"], "created": at(6, 30),
                               "endings": {"/x/a.jsonl": {
                                   "ran_for": 2485,
                                   "reply": "Edvard — the work is done.",
                                   "waiting_on": None}}}}
        text, _ = format_report([row], newest=1261, error=None)
        assert "Edvard — the work is done." in text
