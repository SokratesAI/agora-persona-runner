"""Tests for `tools.survey`.

Nothing here touches the vault: every test hands the module text or a
tmp_path file, and the one test that exercises the no-`--file` path
substitutes the fetcher.

The exit contract is what most of these protect, in both directions. A file
with no survey in it must exit 2 so a cycle posts the first one; a survey he
has answered must exit 2 until a cycle records reading it, and 0 afterwards;
a survey still waiting on him must exit **0** rather than re-asking, because
stacking unanswered copies is the failure this cadence is built to avoid;
and a vault path that cannot be read must exit 1 rather than reading as an
empty file, which would post a duplicate of a survey already on his phone.
"""

import datetime

import pytest

from tools import survey


TODAY = datetime.date(2026, 8, 30)

FRONTMATTER = "---\ntype: log\n---\n"

ANSWERED = """---
type: log
---

## 2026-08-20

1. How useful was my work to you this week? (1-5, and one line on why)
  - 4, the notification fix landed
2. How well could you tell what I was doing and why? (1-5)
  - 
"""


def _post(text, date="2026-08-30"):
    return survey.post(text, date)


def test_render_carries_every_question_and_an_empty_bullet_each():
    block = survey.render("2026-08-30")
    assert block.startswith("## 2026-08-30")
    for n, question in enumerate(survey.QUESTIONS, start=1):
        assert "%d. %s" % (n, question) in block
    assert block.count("\n  - ") == len(survey.QUESTIONS)


def test_survey_stays_short_and_grades_itself():
    # Both halves are his words, not a preference of mine: *"They can't be
    # to long"* and *"make one of the last questions to be on the survey
    # itself to improve it"*.
    assert len(survey.QUESTIONS) <= 5
    assert "survey" in survey.QUESTIONS[-1].lower()


def test_parse_reads_answers_back_and_an_empty_bullet_is_not_an_answer():
    sections = survey.parse(ANSWERED)
    assert len(sections) == 1
    assert sections[0].date == "2026-08-20"
    assert sections[0].answers[0][1] == "4, the notification fix landed"
    assert sections[0].answers[1][1] == ""
    assert sections[0].answered is True


def test_a_partly_answered_survey_counts_as_answered():
    # He may skip a question he has no opinion about. Requiring all five
    # would leave a real reply unread forever.
    sections = survey.parse(ANSWERED)
    assert sum(1 for _, a in sections[0].answers if a) == 1
    assert sections[0].answered is True


def test_an_untouched_survey_is_not_answered():
    text = _post(FRONTMATTER)
    assert survey.parse(text)[0].answered is False


def test_post_puts_the_survey_under_the_frontmatter_not_above_it():
    text = _post(FRONTMATTER)
    assert text.startswith("---\ntype: log\n---\n")
    body = text.split("---\n", 2)[2]
    assert body.lstrip().startswith("## 2026-08-30")


def test_post_keeps_the_older_survey_below_the_new_one():
    text = _post(ANSWERED)
    assert text.index("## 2026-08-30") < text.index("## 2026-08-20")
    assert "4, the notification fix landed" in text


def test_due_when_the_file_holds_no_survey():
    due, reason = survey.is_due(survey.parse(FRONTMATTER), TODAY)
    assert due is True
    assert "no survey has ever been posted" == reason


def test_not_due_while_the_newest_survey_is_unanswered():
    text = survey.post(FRONTMATTER, "2026-07-01")
    due, reason = survey.is_due(survey.parse(text), TODAY)
    assert due is False
    assert "waiting on him" in reason


def test_due_exactly_on_the_interval_and_not_a_day_before():
    # On the boundary in both directions: a threshold test one step off the
    # edge passes whichever side the rule is written on.
    on = datetime.date(2026, 8, 20) + datetime.timedelta(days=survey.INTERVAL_DAYS)
    before = on - datetime.timedelta(days=1)
    assert survey.is_due(survey.parse(ANSWERED), on)[0] is True
    assert survey.is_due(survey.parse(ANSWERED), before)[0] is False


def test_newest_is_chosen_by_date_not_by_position():
    text = ANSWERED + survey.render("2026-08-29")
    assert survey.newest(survey.parse(text)).date == "2026-08-29"


def test_an_answered_survey_is_unread_until_a_cycle_marks_it():
    sections = survey.parse(ANSWERED)
    assert [s.date for s in survey.unread(sections)] == ["2026-08-20"]
    marked = survey.mark_read(ANSWERED, "2026-08-20", "657")
    assert survey.parse(marked)[0].read_by == "657"
    assert survey.unread(survey.parse(marked)) == []
    # The stamp must not eat the answers it is stamping.
    assert survey.parse(marked)[0].answers[0][1] == "4, the notification fix landed"


def test_exit_two_when_answers_are_unread_and_zero_once_read(tmp_path, capsys):
    path = tmp_path / "survey.md"
    path.write_text(ANSWERED, encoding="utf-8")
    assert survey.main(["--file", str(path), "--today", "2026-08-21"]) == 2
    assert "ANSWERED AND UNREAD" in capsys.readouterr().out
    assert survey.main(["--file", str(path), "--mark-read", "2026-08-20",
                        "--cycle", "657",
                        "--reply", "I shipped the notification fix."]) == 0
    assert survey.main(["--file", str(path), "--today", "2026-08-21"]) == 0


def test_exit_two_when_due_and_the_post_then_clears_it(tmp_path):
    path = tmp_path / "survey.md"
    path.write_text(ANSWERED, encoding="utf-8")
    marked = survey.mark_read(ANSWERED, "2026-08-20", "657")
    path.write_text(marked, encoding="utf-8")
    assert survey.main(["--file", str(path), "--today", "2026-08-30"]) == 2
    assert survey.main(["--file", str(path), "--post",
                        "--today", "2026-08-30"]) == 0
    assert survey.main(["--file", str(path), "--today", "2026-08-30"]) == 0


def test_an_unreadable_vault_path_is_cannot_see_not_an_empty_file(capsys):
    assert survey.main([], fetch=lambda path: None) == 1
    out = capsys.readouterr().out
    assert "CANNOT SEE" in out
    assert "DUE" not in out


def test_a_fetched_file_is_judged_the_same_as_one_on_disk(capsys):
    seen = []

    def fetch(path):
        seen.append(path)
        return ANSWERED

    assert survey.main(["--today", "2026-08-30"], fetch=fetch) == 2
    # It has to ask for the file in his own folder, not one of mine.
    assert seen == ["projects/sokrates/projects/nova/survey.md"]
    assert "ANSWERED AND UNREAD" in capsys.readouterr().out


def test_post_refuses_without_a_file_because_the_caller_owns_the_swap(capsys):
    assert survey.main(["--post"], fetch=lambda path: ANSWERED) == 1
    assert "compare-and-swap" in capsys.readouterr().out


# --- The reply, which is the contract his 3-of-5 asked for -------------
#
# He rated the survey itself 3 of 5 on 2026-08-30: *"Maybe, depends what you
# do with it"*. A `— read Cycle N` stamp on a heading answers "did anyone
# read this" and nothing else, so `--mark-read` refuses without `--reply`
# and the reply is written where he typed the answers.


def test_mark_read_refuses_without_a_reply(tmp_path, capsys):
    path = tmp_path / "survey.md"
    path.write_text(ANSWERED, encoding="utf-8")
    assert survey.main(["--file", str(path), "--mark-read", "2026-08-20",
                        "--cycle", "714"]) == 1
    assert "--reply" in capsys.readouterr().out
    # And it wrote nothing: a refusal that half-writes is worse than one
    # that does not write at all.
    assert path.read_text(encoding="utf-8") == ANSWERED


def test_mark_read_with_a_reply_writes_it_under_his_answers(tmp_path):
    path = tmp_path / "survey.md"
    path.write_text(ANSWERED, encoding="utf-8")
    assert survey.main(["--file", str(path), "--mark-read", "2026-08-20",
                        "--cycle", "714",
                        "--reply", "Both halves are on /plan now."]) == 0
    written = path.read_text(encoding="utf-8")
    assert "**Nova, Cycle 714:** Both halves are on /plan now." in written
    # Under his last answer, inside his own section -- not appended to the
    # file, which is where it would be if the section end were mis-found.
    assert written.index("4, the notification fix landed") < written.index(
        "**Nova, Cycle 714:**")
    # And it stamps the heading in the same call, so the two can never
    # disagree about whether this survey was handled.
    assert "## 2026-08-20 — read Cycle 714" in written
    assert survey.main(["--file", str(path), "--today", "2026-08-25"]) == 0


def test_a_reply_lands_inside_its_own_section_not_the_next_one():
    two = ANSWERED + "\n## 2026-08-13\n\n1. Older question\n  - older answer\n"
    written = survey.add_reply(two, "2026-08-20", "714", "the newer one")
    head = written.index("**Nova, Cycle 714:**")
    assert head < written.index("## 2026-08-13")
    assert survey.parse(written)[0].replied is True
    assert survey.parse(written)[1].replied is False


def test_a_reply_is_not_read_back_as_something_he_typed():
    written = survey.add_reply(ANSWERED, "2026-08-20", "714",
                               "I moved the plan onto /plan.")
    section = survey.parse(written)
    answers = [a for _, a in section[0].answers]
    assert answers == ["4, the notification fix landed", ""]


def test_a_second_reply_is_refused_rather_than_stacked():
    once = survey.add_reply(ANSWERED, "2026-08-20", "714", "first")
    with pytest.raises(ValueError, match="already carries a reply"):
        survey.add_reply(once, "2026-08-20", "715", "second")


def test_a_reply_to_a_date_that_is_not_there_is_refused():
    with pytest.raises(ValueError, match="no survey section dated"):
        survey.add_reply(ANSWERED, "2026-07-01", "714", "nothing to answer")


@pytest.mark.parametrize("bad", [
    "## 2026-08-21",
    "3. What should I do differently?",
    "  - pretending to be his answer",
])
def test_a_reply_that_would_corrupt_his_file_is_refused(bad):
    with pytest.raises(ValueError, match="would parse as part of his survey"):
        survey.add_reply(ANSWERED, "2026-08-20", "714", "fine line\n" + bad)


def test_the_report_names_the_command_that_answers_him(capsys):
    assert survey.main(["--today", "2026-08-30"],
                       fetch=lambda path: ANSWERED) == 2
    out = capsys.readouterr().out
    assert "--reply" in out
    assert "--mark-read 2026-08-20" in out
