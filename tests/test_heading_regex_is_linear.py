"""The heading and wiki-link patterns must stay linear in the line length.

CodeQL reported four `py/polynomial-redos` alerts against `nova_boards`
(#39-#42) and the blowup is real: a lazy `(.+?)` in front of `[ \t]*$`
retries the trailing-whitespace split at every position it grows through,
so one 40,000-character heading line took 4.9 seconds to parse. The board
markdown these patterns run over is a vault document, and a cycle -- or
the owner, from his phone -- can put an arbitrarily long line in one.

A timing test needs a threshold, and a threshold is a number I have to
defend, so these assert on the *growth shape* rather than on a wall-clock
budget: quadrupling the input costs a linear pattern 4x and a quadratic
one 16x.

That growth ratio is still a wall-clock reading underneath, and on a shared
CI runner it drifted: this file failed three `main` and PR builds on diffs
touching nothing near it -- 8.27x on 2026-09-07 and 8.508x on 2026-09-09,
against a hardcoded ceiling of 8.0, both green on a re-run with no code
change, and both merged on that re-run. A ratio of two timings taken minutes
apart carries whatever the machine was doing in between, and the honest
subjects here measure 3.7x-4.1x, so there was only a factor of two between a
passing pattern and a red build.

So the ratio is now divided by a *control* pattern's own ratio, measured in
the same process and interleaved reading-for-reading with the subject
(`_CONTROL` below). The control has no backtracking at all, so its cost is
linear in the line length by construction and its SMALL->LARGE ratio is this
machine's reading of what linear costs right now. A slowdown that inflates
the subject's large readings inflates the control's too, and cancels in the
quotient. Linear over linear is ~1 and quadratic over linear is ~4, so the
ceiling is 2, halfway between those on a log scale -- the same derivation as
the 8 it replaces, one level up. Measured on this box at 8k and 32k: the
subjects come in at 0.87x-1.00x normalised and the old patterns at ~3.8x.

The three control tests at the bottom run the *old* patterns through the same
helper and require them to blow the ceiling, so a rewrite that silently made
the assertion unfalsifiable fails here rather than passing quietly.
"""

import re
import sys
import time

import pytest

from agora_runner import md_sections, nova_boards, nova_comments


def _seconds(pattern, subject, run):
    start = time.perf_counter()
    run(pattern, subject)
    return time.perf_counter() - start


SMALL, LARGE = 8000, 32000  # a 4x step, so linear is ~4x and quadratic ~16x
CEILING = 2.0  # normalised against the control: linear ~1, quadratic ~4


REPEATS = 5


def _growth_ratio(pattern, build, run):
    """The subject's SMALL->LARGE growth divided by the control's own.

    Fastest rather than slowest, on both readings. Timing noise on a shared
    CI runner is one-sided -- a descheduled process only ever takes *longer*
    than the work it did -- so the minimum of several readings is the closest
    estimate of the real cost, and it is the only summary that does not carry
    the noise into the answer.

    This used to take the *worst* of three, on both readings, to stop a
    hiccup in the SMALL reading from deflating the ratio and making a
    quadratic pattern look linear. That reasoning is right about the
    denominator and buys it by inflating the numerator, which is where a
    false failure comes from: on 2026-09-07 a `main` build failed here at
    8.27x on a pattern that measures 1.5x-4x, and the merge under it had
    touched nothing but a workflow file. Taking the minimum answers the
    original worry directly -- the minimum SMALL reading is the least
    inflated one available -- rather than trading one bias for another.

    The controls at the bottom of this file are what keep that honest: a
    genuinely quadratic pattern's fastest reading is still ~16x its own
    fastest small reading, so min/min cannot make one look linear.

    Taking the minimum is not enough on its own, though, and that is what
    this file's third false failure showed: the minimum reads through a
    single descheduled reading, and cannot read through a stretch where
    every reading is slow. So the subject's ratio is divided by the
    control's, and the two are measured alternately at each size -- the
    interleaving is the whole mechanism, because a drift only cancels if
    both patterns were read while it was happening.
    """
    def fastest_pair(n):
        subject_line, control_line = build(n), _CONTROL_BUILD(n)
        subject, control = [], []
        for _ in range(REPEATS):
            subject.append(_seconds(pattern, subject_line, run))
            control.append(_seconds(_CONTROL, control_line, _CONTROL_RUN))
        return min(subject), min(control)

    subject_small, control_small = fastest_pair(SMALL)
    subject_large, control_large = fastest_pair(LARGE)
    # Too fast to divide by; the pattern is not the problem.
    subject_ratio = subject_large / max(subject_small, 1e-6)
    control_ratio = control_large / max(control_small, 1e-6)
    if control_ratio <= 0:  # pragma: no cover - a scan cannot cost nothing
        raise AssertionError("the linear control measured no growth at all")
    return subject_ratio / control_ratio


MATCH = lambda pattern, subject: pattern.match(subject)
FINDALL = lambda pattern, subject: pattern.findall(subject)

# The reference every subject is measured against. `\[\[` over a line of
# `[[` has no backtracking and no optional part: the engine scans the line
# once, so its cost is linear in the length by construction and there is no
# input that could make it anything else. It is deliberately one of the
# slower candidates measured (210us at SMALL against 4.6us for a bare
# `[\t]*b`), because a control whose reading is mostly Python call overhead
# would come back at ~1x whatever the machine was doing and normalise
# nothing. Measured ratio on this box: 4.21x, against a true linear 4.
_CONTROL = re.compile(r"\[\[")
_CONTROL_BUILD = lambda n: "[[" * n
_CONTROL_RUN = FINDALL


@pytest.mark.parametrize("pattern,build,run", [
    # A heading whose title holds a long run of tabs before its last
    # non-space character -- the shape the old lazy group walked twice.
    (nova_boards._SECTION_RE, lambda n: "## a" + "\t" * n + "b", MATCH),
    (nova_comments._SECTION_RE, lambda n: "## a" + "\t" * n + "b", MATCH),
    (md_sections._SECTION_RE, lambda n: "## a" + "\t" * n + "b", MATCH),
    (nova_boards._DETAIL_RE, lambda n: "## 12 — a" + "\t" * n + "b", MATCH),
    # The same line with NO non-space character in it at all, so the match
    # fails. This is the case the first version of this fix still had, and
    # the case that only shows up when the pattern cannot match: `[ \t]+` and
    # `.*` both accept a tab, so the engine tries every way of dividing the
    # run between them before giving up. CodeQL caught it; my benchmark did
    # not, because I had only ever timed inputs that matched.
    (nova_boards._SECTION_RE, lambda n: "## " + "\t" * n, MATCH),
    (nova_comments._SECTION_RE, lambda n: "## " + "\t" * n, MATCH),
    (md_sections._SECTION_RE, lambda n: "## " + "\t" * n, MATCH),
    (nova_boards._DETAIL_RE, lambda n: "## 12 — " + "\t" * n, MATCH),
    # A run of unclosed `[[`, which the old `[^\]]*` scanned to the end of
    # the line from every one of them.
    (nova_boards._WIKILINK_RE, lambda n: "| " + "[[" * n + " |", FINDALL),
])
def test_pattern_is_linear_in_the_line_length(pattern, build, run):
    ratio = _growth_ratio(pattern, build, run)
    assert ratio < CEILING, (
        f"{pattern.pattern!r} grew {ratio:.2f}x as fast as a linear control "
        "over the same 4x step -- linear costs ~1x here, quadratic ~4x"
    )


def test_the_old_lazy_heading_shape_blows_the_ceiling():
    """A control: the pattern this fix replaced fails the assertion above.

    Without it every case above could be passing on a pattern that was
    never quadratic, and the test would prove nothing.
    """
    old = re.compile(r"^(#{1,2})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
    ratio = _growth_ratio(old, lambda n: "## a" + "\t" * n + "b", MATCH)
    assert ratio > CEILING


def test_the_first_attempt_at_this_fix_blows_the_ceiling():
    """The control for the case I got wrong once already.

    `(.*[^ \t])` alone kills the quadratic on a line that matches and leaves
    it standing on a line that does not. Without possessive quantifiers this
    takes 1.1 seconds on 16,000 tabs.
    """
    half_fixed = re.compile(r"^(#{1,2})[ \t]+(.*[^ \t])[ \t]*$", re.MULTILINE)
    ratio = _growth_ratio(half_fixed, lambda n: "## " + "\t" * n, MATCH)
    assert ratio > CEILING


def test_the_old_wikilink_class_blows_the_ceiling():
    r"""The same control for `[^\]]*`, which is a different failure shape.

    The heading patterns are quadratic inside one match attempt; this one
    is quadratic across start positions, so one control does not cover it.
    """
    old = re.compile(r"\[\[[^\]]*\]\]")
    ratio = _growth_ratio(old, lambda n: "| " + "[[" * n + " |", FINDALL)
    assert ratio > CEILING


def test_a_heading_of_only_whitespace_is_no_longer_a_heading():
    """The one input whose answer changed, asserted rather than left latent.

    `## ` followed by nothing but spaces used to match with a single space
    as its title. It now does not match at all, which is the honest reading
    and is what every caller of these already wanted.
    """
    for pattern in (nova_boards._SECTION_RE,
                    nova_comments._SECTION_RE,
                    md_sections._SECTION_RE):
        assert pattern.match("##   \t ") is None
    assert nova_boards._SECTION_RE.match("## Board").group(2) == "Board"


def test_titles_are_still_stripped_of_trailing_whitespace():
    assert nova_boards._SECTION_RE.match("##  Board \t ").group(2) == "Board"
    assert nova_comments._SECTION_RE.match("## New \t").group("name") == "New"
    assert md_sections._SECTION_RE.match("## New \t").group("name") == "New"
    detail = nova_boards._DETAIL_RE.match("### #57 — More pages  \t")
    assert detail.group(3) == "57" and detail.group(4) == "More pages"
    assert nova_boards._DETAIL_RE.match("### #57 —  ").group(4) == ""


def test_wikilink_still_masks_the_pipe_it_was_written_for():
    line = "| [[#57 — More pages in the Nova app|57]] | title | 🟡 | 08-11 |"
    assert nova_boards._WIKILINK_RE.findall(line) == [
        "[[#57 — More pages in the Nova app|57]]"
    ]


def test_one_slow_reading_does_not_fail_a_linear_pattern():
    """The regression this estimator change is for.

    A single descheduled reading at LARGE used to be the answer, because the
    ratio was worst-over-worst. Here a fake pattern costs the same at both
    sizes -- perfectly flat, so the honest ratio is 1 -- and one reading in
    the run is a hundred times slower. Taking the minimum reads through it.
    """
    calls = {"n": 0}

    class FlatPattern:
        pattern = "<flat>"

        def match(self, subject):
            calls["n"] += 1
            # One hiccup, in the LARGE half: the SMALL readings come first.
            time.sleep(0.02 if calls["n"] == REPEATS + 1 else 0.0002)

    ratio = _growth_ratio(FlatPattern(), lambda n: "x" * n, MATCH)
    assert calls["n"] == 2 * REPEATS, "both sizes should be sampled REPEATS times"
    assert ratio < CEILING, (
        f"a flat pattern with one slow reading measured {ratio:.1f}x -- the "
        "estimator is carrying the hiccup into the answer"
    )


def test_a_sustained_slowdown_cancels_against_the_control(monkeypatch):
    """The regression the normalisation is for, and the one `min()` cannot fix.

    A single descheduled reading is what the test above covers, and taking the
    minimum reads straight through it. The three CI failures that put this
    control here were not that shape: every reading in the LARGE phase was
    slow, and the minimum of five slow readings is still slow. Here a flat
    subject and a flat control -- both genuinely 1x, no growth at all -- are
    read through a machine that is ten times slower for the whole LARGE phase.
    Unnormalised that is 10.0x and blows any ceiling; against the control it
    is 1.0x, which is the truth about the patterns.
    """
    # This module, not a re-import of it: `tests` is a namespace package, so
    # `import tests.test_heading_regex_is_linear` loads a SECOND copy and the
    # monkeypatch lands on globals `_growth_ratio` never reads. That is how
    # this test first "failed" at 0.54x.
    mod = sys.modules[__name__]

    SLOWDOWN = 10

    class Flat:
        pattern = "<flat>"

        def match(self, subject):
            # Flat in the input size: the machine is the only thing changing.
            time.sleep(0.002 * (SLOWDOWN if len(subject) == LARGE else 1))

    monkeypatch.setattr(mod, "_CONTROL", Flat())
    monkeypatch.setattr(mod, "_CONTROL_BUILD", lambda n: "x" * n)
    monkeypatch.setattr(mod, "_CONTROL_RUN", MATCH)

    ratio = _growth_ratio(Flat(), lambda n: "x" * n, MATCH)
    assert ratio == pytest.approx(1.0, rel=0.4), (
        f"a {SLOWDOWN}x sustained slowdown over two flat patterns measured "
        f"{ratio:.2f}x -- it is not cancelling against the control"
    )
    assert ratio < CEILING
