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
import json
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
    code = mr.report(["anything"], store=str(tmp_path / "nope"), out=out, ledger=None)
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
    assert mr.report(["kubernetes"], store=store, out=out, ledger=None) == 0
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
    mr.report(["couchdb", "backups"], store=store, out=out, ledger=None)
    assert "both.md" in out.getvalue()
    assert "one.md" not in out.getvalue()

    out = io.StringIO()
    mr.report(["couchdb", "backups"], store=store, require_all=False, out=out, ledger=None)
    assert "one.md" in out.getvalue()


def test_slug_match_outranks_a_body_match(tmp_path):
    store = str(tmp_path)
    write(store, "sealed-secrets.md", "unrelated description", "nothing here")
    write(store, "other.md", "unrelated", "mentions sealed secrets in passing")
    out = io.StringIO()
    mr.report(["sealed"], store=store, out=out, ledger=None)
    lines = [l for l in out.getvalue().splitlines() if l.strip().startswith("1.")]
    assert "sealed-secrets.md" in lines[0]


def test_no_match_is_reported_as_a_negative_over_the_whole_store(tmp_path):
    store = str(tmp_path)
    write(store, "one.md", "about couchdb", "x", index=mr.LOADED_INDEX)
    out = io.StringIO()
    assert mr.report(["helicopter"], store=store, out=out, ledger=None) == 0
    assert "not over the index" in out.getvalue()


def test_full_bound_lists_every_hit_and_prints_only_the_top_n(tmp_path):
    store = str(tmp_path)
    for i in range(4):
        write(store, "hit%d.md" % i, "about quota", "body %d is here" % i)
    out = io.StringIO()
    mr.report(["quota"], store=store, full=2, out=out, ledger=None)
    text = out.getvalue()
    # Nothing is hidden: all four are listed...
    for i in range(4):
        assert "hit%d.md" % i in text
    # ...but only two bodies are printed, and the report says so.
    assert text.count("=====") == 4  # two headers, each with two ===== runs
    assert "2 more hit(s) listed above without their text" in text


def test_a_capitalised_term_still_matches(tmp_path):
    store = str(tmp_path)
    write(store, "one.md", "about kubernetes", "x")
    out = io.StringIO()
    mr.report(["Kubernetes"], store=store, out=out, ledger=None)
    assert "one.md" in out.getvalue()


def test_a_recall_is_recorded_with_its_archive_only_count(tmp_path):
    """The ledger has to separate a hit from a hit the index withheld."""
    store = str(tmp_path / "store")
    os.makedirs(store)
    write(store, "loaded-one.md", "about kubernetes", "body",
          index=mr.LOADED_INDEX)
    write(store, "archived-one.md", "also kubernetes", "body",
          index=mr.ARCHIVE_INDEX)
    ledger = str(tmp_path / "recall.jsonl")

    out = io.StringIO()
    assert mr.report(["kubernetes"], store=store, out=out, ledger=ledger) == 0

    rows = [json.loads(l) for l in open(ledger) if l.strip()]
    assert len(rows) == 1
    assert rows[0]["hits"] == 2
    assert rows[0]["archive_only"] == 1
    assert rows[0]["terms"] == ["kubernetes"]
    assert rows[0]["cwd"] == os.getcwd()


def test_a_recall_that_found_nothing_is_recorded_too(tmp_path):
    """A miss is the finding that the store lacks something; do not drop it."""
    store = str(tmp_path / "store")
    os.makedirs(store)
    write(store, "one.md", "about kubernetes", "body")
    ledger = str(tmp_path / "recall.jsonl")

    mr.report(["helicopter"], store=store, out=io.StringIO(), ledger=ledger)

    rows = [json.loads(l) for l in open(ledger) if l.strip()]
    assert len(rows) == 1
    assert rows[0]["hits"] == 0
    assert rows[0]["top"] is None


def test_an_unwritable_ledger_says_so_and_still_answers(tmp_path):
    """Recording must never cost the answer -- but must not fail silently."""
    store = str(tmp_path / "store")
    os.makedirs(store)
    write(store, "one.md", "about kubernetes", "body")
    out = io.StringIO()

    code = mr.report(["kubernetes"], store=store, out=out,
                     ledger=str(tmp_path / "no-such-dir" / "recall.jsonl"))

    assert code == 0
    text = out.getvalue()
    assert "not recorded" in text
    assert "one.md" in text


def test_usage_on_a_missing_ledger_is_no_instrument_not_a_zero(tmp_path):
    """The failure this guards: an absent ledger reading as 'never used'."""
    out = io.StringIO()
    code = mr.summarise_usage(ledger=str(tmp_path / "nope.jsonl"), out=out)
    assert code == 1
    assert "NO RECALL LEDGER" in out.getvalue()
    assert "not a measured zero" in out.getvalue()


def test_usage_counts_calls_surfaced_archives_and_callers(tmp_path):
    ledger = str(tmp_path / "recall.jsonl")
    with open(ledger, "w") as fh:
        for row in [
            {"ts": "2026-09-23T21:00:00+00:00", "cwd": "/data/workspace/a",
             "terms": ["x"], "hits": 2, "archive_only": 1, "top": "a.md"},
            {"ts": "2026-09-24T21:00:00+00:00", "cwd": "/data/workspace/a",
             "terms": ["y"], "hits": 0, "archive_only": 0, "top": None},
            {"ts": "2026-09-25T21:00:00+00:00", "cwd": "/data/other",
             "terms": ["z"], "hits": 1, "archive_only": 0, "top": "b.md"},
        ]:
            fh.write(json.dumps(row) + "\n")

    out = io.StringIO()
    assert mr.summarise_usage(ledger=ledger, out=out) == 0
    text = out.getvalue()
    assert "3 recall(s) recorded" in text
    assert "2 found something, 1 found nothing" in text
    assert "1 surfaced at least one archive-only memory" in text
    assert "2  /data/workspace/a" in text
    assert "1  /data/other" in text
    assert "2026-09-23T21:00:00+00:00 .. 2026-09-25T21:00:00+00:00" in text
