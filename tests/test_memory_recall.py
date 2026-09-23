"""Tests for `tools.memory_recall`.

The tool exists because the loaded index is a sixth of the store, so the
two things pinned hardest here are the two that make it lie in the
direction that reads as fine:

- a store that cannot be read is exit 1 and says so, never exit 0 with
  an empty result that reads as "nothing remembered";
- a hit that is only in the archive is labelled as such, because
  "I already knew this" and "I have known this and it never reached me"
  are different findings.

Every test builds a real store on disk and asserts on the printed
report, not on the return value alone -- the report is the product.
"""
import io
import os

from tools import memory_recall as mr


def write(store, name, description, body, index=None):
    os.makedirs(store, exist_ok=True)
    text = ("---\nname: %s\ndescription: %s\nmetadata:\n  type: reference\n"
            "---\n\n%s\n" % (name[:-3], description, body))
    open(os.path.join(store, name), "w", encoding="utf-8").write(text)
    if index:
        with open(os.path.join(store, index), "a", encoding="utf-8") as fh:
            fh.write("- [%s](%s) — %s\n" % (name, name, description))


def test_unreadable_store_is_exit_1_and_says_it_is_not_an_empty_memory(tmp_path):
    out = io.StringIO()
    code = mr.report(["anything"], store=str(tmp_path / "nope"), out=out)
    assert code == 1
    text = out.getvalue()
    assert "CANNOT READ THE MEMORY STORE" in text
    # The whole point: an unreadable store must not read as a clean negative.
    assert "not 'nothing remembered'" in text


def test_archive_only_hit_is_labelled_as_never_having_reached_me(tmp_path):
    store = str(tmp_path)
    write(store, "loaded-one.md", "a loaded fact about kubernetes", "body one",
          index=mr.LOADED_INDEX)
    write(store, "archived-one.md", "an archived fact about kubernetes",
          "body two", index=mr.ARCHIVE_INDEX)
    out = io.StringIO()
    assert mr.report(["kubernetes"], store=store, out=out) == 0
    text = out.getvalue()
    assert "loaded-one.md  (loaded)" in text
    assert "ARCHIVED -- this one never reached me" in text
    # And the header states the split rather than leaving it to be inferred.
    assert "1 indexed in %s" % mr.LOADED_INDEX in text


def test_all_terms_must_match_unless_any_is_asked_for(tmp_path):
    store = str(tmp_path)
    write(store, "both.md", "about couchdb and backups", "x")
    write(store, "one.md", "about couchdb only", "x")
    out = io.StringIO()
    mr.report(["couchdb", "backups"], store=store, out=out)
    assert "both.md" in out.getvalue()
    assert "one.md" not in out.getvalue()

    out = io.StringIO()
    mr.report(["couchdb", "backups"], store=store, require_all=False, out=out)
    assert "one.md" in out.getvalue()


def test_slug_match_outranks_a_body_match(tmp_path):
    store = str(tmp_path)
    write(store, "sealed-secrets.md", "unrelated description", "nothing here")
    write(store, "other.md", "unrelated", "mentions sealed secrets in passing")
    out = io.StringIO()
    mr.report(["sealed"], store=store, out=out)
    lines = [l for l in out.getvalue().splitlines() if l.strip().startswith("1.")]
    assert "sealed-secrets.md" in lines[0]


def test_no_match_is_reported_as_a_negative_over_the_whole_store(tmp_path):
    store = str(tmp_path)
    write(store, "one.md", "about couchdb", "x", index=mr.LOADED_INDEX)
    out = io.StringIO()
    assert mr.report(["helicopter"], store=store, out=out) == 0
    assert "not over the index" in out.getvalue()


def test_full_bound_lists_every_hit_and_prints_only_the_top_n(tmp_path):
    store = str(tmp_path)
    for i in range(4):
        write(store, "hit%d.md" % i, "about quota", "body %d is here" % i)
    out = io.StringIO()
    mr.report(["quota"], store=store, full=2, out=out)
    text = out.getvalue()
    # Nothing is hidden: all four are listed...
    for i in range(4):
        assert "hit%d.md" % i in text
    # ...but only two bodies are printed, and the report says so.
    assert text.count("=====") == 4  # two headers, each with two ===== runs
    assert "2 more hit(s) listed above without their text" in text
