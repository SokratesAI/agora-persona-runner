"""Tests for `tools.memory_index_health`.

The thing under test is a judgement about a number, so every test here
builds a real store on disk and a real fake binary and asserts the exit
code plus the sentence that justifies it. Two things are deliberately
pinned harder than the rest, because getting either wrong makes the check
lie in the direction that reads as fine:

- the caps come out of the binary and a binary without them is exit 1,
  never exit 0;
- the index is counted in code points over trimmed text, not in UTF-8
  bytes and not in `wc -l` lines.
"""
import io
import os
import time

import pytest

from tools import memory_index_health as mih


def _cli(tmp_path, lines=200, chars=25000, anchored=True, ident="gD"):
    """A stand-in for the installed binary, with the loader's declaration."""
    path = tmp_path / "claude.exe"
    filler = b"x" * 4096
    if anchored:
        decl = (
            f'var jl="MEMORY.md",RKt="Memory is paused. Run /pause-memory to '
            f'resume automemory.",{ident}={lines},TB={chars},IKt=4*TB;'
        ).encode()
    else:
        decl = b'var jl="MEMORY.md";function bFe(e){return e.trim()}'
    path.write_bytes(filler + decl + filler)
    return str(path)


def _store(tmp_path, index_lines, memories=1, oldest_days=1.0, line=None):
    store = tmp_path / "nova-memory"
    store.mkdir()
    line = line or "- [A memory](a.md) — a hook"
    (store / "MEMORY.md").write_text("\n".join([line] * index_lines) + "\n")
    now = time.time()
    for i in range(memories):
        f = store / f"m{i}.md"
        f.write_text("x")
        os.utime(f, (now - oldest_days * 86400, now - oldest_days * 86400))
    return str(store)


def _run(store, cli, now=None):
    out = io.StringIO()
    code = mih.report(store=store, cli_path=cli, out=out, now=now)
    return code, out.getvalue()


def test_reads_both_caps_out_of_the_binary(tmp_path):
    assert mih.read_caps(_cli(tmp_path, lines=200, chars=25000)) == (
        200, 25000, None)


def test_cap_anchor_survives_a_renamed_identifier(tmp_path):
    """Minified names change every release; the string literals do not."""
    assert mih.read_caps(_cli(tmp_path, ident="zQ7$x"))[:2] == (200, 25000)


def test_caps_are_found_when_the_anchor_straddles_a_chunk_boundary(
        tmp_path, monkeypatch):
    """The binary is 215MB and read in chunks, so the overlap has to work."""
    path = tmp_path / "claude.exe"
    decl = (b'var jl="MEMORY.md",RKt="Memory is paused. x",gD=200,TB=25000,')
    # Put the declaration so it spans the seam of a tiny chunk size.
    path.write_bytes(b"y" * 50 + decl + b"y" * 50)
    real_open = open

    class _Chunked(io.BytesIO):
        def read(self, _n=None):  # force a 60-byte chunk, mid-declaration
            return super().read(60)

    def fake_open(p, mode="r", *a, **k):
        if str(p) == str(path):
            return _Chunked(real_open(p, "rb").read())
        return real_open(p, mode, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert mih.read_caps(str(path))[:2] == (200, 25000)


def test_a_binary_without_the_anchor_is_no_instrument_not_clean(tmp_path):
    """Exit 1, never 0 -- 'I could not find the caps' is not 'it fits'."""
    cli = _cli(tmp_path, anchored=False)
    code, text = _run(_store(tmp_path, 10), cli)
    assert code == 1
    assert "NO INSTRUMENT" in text
    assert "would be guesses" in text


def test_a_missing_binary_is_no_instrument(tmp_path):
    code, text = _run(_store(tmp_path, 10), str(tmp_path / "gone.exe"))
    assert code == 1
    assert "no CLI at" in text


def test_an_unreadable_index_is_no_instrument(tmp_path):
    store = tmp_path / "empty"
    store.mkdir()
    code, text = _run(str(store), _cli(tmp_path))
    assert code == 1
    assert "could not read" in text
    assert "not the same as it fitting" in text


def test_the_index_is_counted_in_code_points_not_utf8_bytes(tmp_path):
    """An em-dash is 1 to the loader and 3 to `wc -c`."""
    store = tmp_path / "s"
    store.mkdir()
    (store / "MEMORY.md").write_text("— — —\n")
    lines, chars, longest, err = mih.measure_index(
        str(store / "MEMORY.md"))
    assert err is None
    assert chars == 5 and lines == 1 and longest == 5


def test_the_index_is_counted_over_trimmed_text(tmp_path):
    """A trailing newline is not a 201st line."""
    store = tmp_path / "s"
    store.mkdir()
    (store / "MEMORY.md").write_text("a\nb\n\n\n")
    assert mih.measure_index(str(store / "MEMORY.md"))[0] == 2


def test_over_the_line_cap_is_truncating_now(tmp_path):
    code, text = _run(_store(tmp_path, 201), _cli(tmp_path))
    assert code == 2
    assert "TRUNCATING" in text
    assert "201 line(s) against a cap of 200" in text
    assert "do NOT delete a memory file" in text


def test_over_the_character_cap_alone_is_truncating(tmp_path):
    """The two caps are AND, so the smaller one is enough on its own."""
    code, text = _run(_store(tmp_path, 10), _cli(tmp_path, chars=20))
    assert code == 2
    assert "character(s) against a cap of 20" in text
    assert "line(s) against a cap" not in text.split("TRUNCATING")[1]


def test_thin_headroom_acts_before_it_truncates(tmp_path):
    """20 lines left against 25 memories a day is under the re-run interval."""
    store = _store(tmp_path, 180, memories=25, oldest_days=1.0)
    code, text = _run(store, _cli(tmp_path))
    assert code == 2
    assert "ACT NOW" in text
    assert "0.8 day(s) of headroom" in text
    assert "TRUNCATING" not in text


def test_wide_headroom_is_clean(tmp_path):
    store = _store(tmp_path, 20, memories=20, oldest_days=20.0)
    code, text = _run(store, _cli(tmp_path))
    assert code == 0
    assert "the whole index reaches the session" in text
    assert "NOT JUDGED" in text


def test_the_character_cap_can_bind_before_the_line_cap(tmp_path):
    """A store of long hooks runs out of characters with lines to spare."""
    store = _store(tmp_path, 50, memories=1, oldest_days=50.0,
                   line="- [x](x.md) " + "y" * 180)
    code, text = _run(store, _cli(tmp_path))
    assert "characters bind first" in text
    assert code == 0


def test_growth_is_not_measurable_without_memories(tmp_path):
    """A count is not a deadline, and the report says so rather than acting."""
    store = _store(tmp_path, 190, memories=0)
    code, text = _run(store, _cli(tmp_path))
    assert code == 0
    assert "NOT MEASURED" in text
    assert "not a deadline" in text


def test_growth_ignores_the_index_and_the_archive(tmp_path):
    store = tmp_path / "s"
    store.mkdir()
    (store / "MEMORY.md").write_text("- a\n")
    (store / "MEMORY-archive.md").write_text("- b\n")
    assert mih.growth_per_day(str(store)) is None


def test_growth_ignores_a_non_markdown_file(tmp_path):
    store = tmp_path / "s"
    store.mkdir()
    (store / "MEMORY.md").write_text("- a\n")
    (store / "notes.txt").write_text("x")
    assert mih.growth_per_day(str(store)) is None


def test_growth_rate_is_files_over_the_span_of_the_oldest(tmp_path):
    now = time.time()
    store = _store(tmp_path, 5, memories=10, oldest_days=2.0)
    rate, files, span = mih.growth_per_day(store, now=now)
    assert files == 10
    assert span == pytest.approx(2.0, abs=0.01)
    assert rate == pytest.approx(5.0, abs=0.05)


def test_a_long_index_line_is_named_without_changing_the_verdict(tmp_path):
    store = _store(tmp_path, 20, memories=20, oldest_days=20.0,
                   line="- [x](x.md) " + "y" * 250)
    code, text = _run(store, _cli(tmp_path, chars=100000))
    assert code == 0
    assert "longest index line is 262 characters" in text


def test_registered_in_preflight_with_a_label_and_a_cadence():
    from tools import preflight
    assert "memory_index_health" in preflight.CHECKS
    assert preflight.unknown_checks(preflight.CHECKS) == []
    assert preflight.unlabelled_checks(preflight.CHECKS) == []
    assert preflight.CADENCE_HOURS["memory_index_health"] == 24.0


def test_lookahead_matches_the_preflight_cadence():
    """The threshold is this check's own blind interval, not a taste."""
    from tools import preflight
    assert mih.LOOKAHEAD_DAYS == (
        preflight.CADENCE_HOURS["memory_index_health"] / 24.0)
