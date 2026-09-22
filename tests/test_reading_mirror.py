"""tools.reading_mirror: reading copies of the owner's documents (issue #238)."""
import pytest

from tools import reading_mirror as rm

SRC = rm.SOURCE_PREFIX
DST = rm.MIRROR_PREFIX
NOW = 10_000_000_000
OLD = NOW - 60 * 60_000      # an hour ago: settled
FRESH = NOW - 5 * 60_000     # five minutes ago: still settling


class FakeVault:
    def __init__(self, files):
        # path -> (content, mtime); a content of None is a tombstone
        self.files = dict(files)
        self.revs = {p: "1-a" for p in files}
        self.writes = []

    def file_docs(self, prefix):
        return {p: {"mtime": m} for p, (c, m) in self.files.items()
                if p.startswith(prefix) and c is not None}

    def read(self, path):
        return self.files.get(path, (None, 0))[0]

    def read_rev(self, path):
        return self.read(path), self.revs.get(path)

    def write(self, path, content, if_rev=None):
        if if_rev != self.revs.get(path):
            return "FAILED(409 conflict)"
        self.writes.append(path)
        self.files[path] = (content, NOW)
        self.revs[path] = "2-b"
        return "written"


def verdicts(vault):
    return {name: v for v, name, _, _ in rm.plan(vault, NOW)}


def test_a_settled_document_is_copied_into_the_obsidian_folder():
    v = FakeVault({SRC + "goals.md": ("---\ntype: note\n---\n# Goals\n", OLD)})
    assert rm.main([], client=v, now_ms=NOW) == 0
    assert v.writes == [DST + "goals.md"]
    copy = v.read(DST + "goals.md")
    assert copy.startswith("---\nmirror_of: " + SRC + "goals.md\nmirror_sha: ")
    assert copy.endswith("type: note\n---\n# Goals\n")


def test_capture_files_and_context_are_not_copied():
    v = FakeVault({SRC + n: ("x", OLD) for n in rm.EXCLUDED})
    assert rm.main([], client=v, now_ms=NOW) == 0
    assert v.writes == []


def test_a_document_changed_minutes_ago_waits_for_the_next_run():
    v = FakeVault({SRC + "roadmap.md": ("# r\n", FRESH)})
    assert verdicts(v) == {"roadmap.md": "SETTLING"}
    rm.main([], client=v, now_ms=NOW)
    assert v.writes == []


def test_a_source_rewritten_every_cycle_is_copied_once_it_falls_behind():
    """The failure this guards: a document a cycle touches every cycle is
    always younger than the settle window, so it would never be copied."""
    v = FakeVault({SRC + "plans.md": ("v3", FRESH)})
    v.files[DST + "plans.md"] = (rm.to_mirror("v1", SRC + "plans.md"),
                                 NOW - 90 * 60_000)
    v.revs[DST + "plans.md"] = "1-a"
    assert verdicts(v) == {"plans.md": "STALE"}
    assert rm.main([], client=v, now_ms=NOW) == 0
    assert rm.from_mirror(v.read(DST + "plans.md"))[1] == "v3"


def test_a_copy_that_is_only_a_little_behind_still_waits():
    v = FakeVault({SRC + "plans.md": ("v2", FRESH)})
    v.files[DST + "plans.md"] = (rm.to_mirror("v1", SRC + "plans.md"),
                                 NOW - 10 * 60_000)
    v.revs[DST + "plans.md"] = "1-a"
    assert verdicts(v) == {"plans.md": "SETTLING"}
    assert v.writes == []


def test_a_brand_new_document_with_no_copy_yet_still_settles():
    v = FakeVault({SRC + "plans.md": ("v1", FRESH)})
    assert verdicts(v) == {"plans.md": "SETTLING"}


def test_an_unchanged_source_is_not_rewritten():
    text = "# r\n"
    v = FakeVault({SRC + "roadmap.md": (text, OLD),
                   DST + "roadmap.md": (rm.to_mirror(text, SRC + "roadmap.md"), OLD)})
    assert verdicts(v) == {"roadmap.md": "CURRENT"}


def test_a_changed_source_replaces_an_untouched_copy():
    v = FakeVault({SRC + "roadmap.md": ("# new\n", OLD),
                   DST + "roadmap.md": (rm.to_mirror("# old\n", SRC + "roadmap.md"), OLD)})
    assert rm.main([], client=v, now_ms=NOW) == 0
    assert v.read(DST + "roadmap.md").endswith("# new\n")


def test_a_copy_he_edited_in_obsidian_is_never_overwritten():
    edited = rm.to_mirror("# old\n", SRC + "roadmap.md") + "my own note\n"
    v = FakeVault({SRC + "roadmap.md": ("# new\n", OLD),
                   DST + "roadmap.md": (edited, OLD)})
    assert verdicts(v) == {"roadmap.md": "EDITED"}
    rm.main([], client=v, now_ms=NOW)
    assert v.read(DST + "roadmap.md") == edited


def test_round_trip_recovers_the_source_with_and_without_frontmatter():
    for text in ("---\na: 1\n---\nbody\n", "no frontmatter\n", ""):
        recorded, original = rm.from_mirror(rm.to_mirror(text, "p"))
        assert original == text and recorded == rm.sha(text)


def test_a_deleted_copy_is_recreated_over_its_tombstone():
    v = FakeVault({SRC + "goals.md": ("# g\n", OLD), DST + "goals.md": (None, OLD)})
    assert rm.main([], client=v, now_ms=NOW) == 0
    assert v.writes == [DST + "goals.md"]


def test_a_refused_write_exits_1():
    v = FakeVault({SRC + "goals.md": ("# g\n", OLD)})
    v.write = lambda *a, **k: "FAILED(409 conflict)"
    assert rm.main([], client=v, now_ms=NOW) == 1


def test_a_copy_whose_source_is_gone_is_reported_and_kept():
    v = FakeVault({DST + "gone.md": (rm.to_mirror("x", SRC + "gone.md"), OLD)})
    assert verdicts(v) == {"gone.md": "ORPHAN"}
    rm.main([], client=v, now_ms=NOW)
    assert DST + "gone.md" in v.files and v.writes == []


def test_the_mirror_folder_is_outside_novas_database():
    from agora_runner import vault
    assert not DST.startswith(tuple(vault.NOVA_DB_FOLDERS))
    assert SRC.startswith(tuple(vault.NOVA_DB_FOLDERS))


DIGEST = rm.EXTRA_SOURCES["journal-digest.md"]


@pytest.fixture(autouse=True)
def only_his_folder(request, monkeypatch):
    """The tests above are about his folder alone; the extra sources are
    opted into by the tests below that name `with_digest`."""
    if "with_digest" not in request.fixturenames:
        monkeypatch.setattr(rm, "EXTRA_SOURCES", {})


@pytest.fixture
def with_digest():
    return DIGEST


def test_the_handoff_digest_is_copied_from_outside_his_folder(with_digest):
    v = FakeVault({DIGEST: ("# Journal — Digest\n", NOW - 10 * 60_000)})
    assert rm.main([], client=v, now_ms=NOW) == 0
    assert v.writes == [DST + "journal-digest.md"]
    assert v.read(DST + "journal-digest.md").startswith(
        "---\nmirror_of: " + DIGEST + "\n")


def test_the_digest_waits_minutes_not_half_an_hour(with_digest):
    # Rewritten once per 24-minute cycle: a 30-minute settle never lands it.
    v = FakeVault({DIGEST: ("d\n", NOW - 2 * 60_000)})
    assert verdicts(v)["journal-digest.md"] == "SETTLING"
    v = FakeVault({DIGEST: ("d\n", NOW - 10 * 60_000),
                   SRC + "goals.md": ("g\n", NOW - 10 * 60_000)})
    assert verdicts(v) == {"journal-digest.md": "COPY", "goals.md": "SETTLING"}


def test_a_missing_extra_source_is_no_instrument_not_nothing_to_copy(with_digest):
    v = FakeVault({})
    assert rm.main([], client=v, now_ms=NOW) == 1
