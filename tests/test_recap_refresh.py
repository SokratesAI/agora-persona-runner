"""The runner-side refresher that rewrites the recap when the journal changes.

His issue #219, and the thing it is actually buying is that no cycle has to
decide anything. So the tests are about the decisions the refresher takes on
its own: when it fires, when it costs nothing, and what it does when Haiku
does not answer. The last of those is the one with teeth -- **the old card
must survive every failure path**, because a blank card is worse than a card
whose stamp says it is four hours old.

Nothing here reaches the vault or the bridge. `refresh_once` takes its I/O
from `agora_runner.vault` and `ask_haiku`, both monkeypatched, so a test that
passes has exercised the real branch rather than a stub of it.
"""

import pytest

from agora_runner import recap_refresh
from agora_runner.nova_recap import MAX_BULLETS, RECAP_PATH, parse_recap, render


def entry(seq, cycle, title="Did a thing", pr=""):
    body = f"### Cycle {cycle} — {title}\n\nSome prose.\n"
    if pr:
        body += f"\nPR: {pr}\n"
    return f"{seq}-cycle-{cycle}.md", body


class FakeVault:
    """The three vault calls `refresh_once` makes, and nothing else."""

    def __init__(self, entries, recap=""):
        self.docs = {recap_refresh.JOURNAL_DIR + name: body
                     for name, body in entries}
        self.docs[RECAP_PATH] = recap
        self.written = []

    def list_ids(self, folder):
        return [p for p in self.docs if p.startswith(folder)]

    def read(self, path):
        return self.docs.get(path)

    def write(self, path, text):
        self.written.append((path, text))
        self.docs[path] = text


@pytest.fixture
def wired(monkeypatch):
    """`refresh_once` with its vault replaced. Returns a factory."""
    def build(entries, recap="", bullets=None, raises=False):
        vault = FakeVault(entries, recap)
        import agora_runner.vault as real
        monkeypatch.setattr(real, "vault_list_ids", vault.list_ids)
        monkeypatch.setattr(real, "vault_read_path", vault.read)
        monkeypatch.setattr(real, "vault_write_path", vault.write)
        asked = []

        def ask(prompt):
            asked.append(prompt)
            if raises:
                raise RuntimeError("bridge exploded")
            return list(bullets or [])

        monkeypatch.setattr(recap_refresh, "ask_haiku", ask)
        vault.asked = asked
        return vault
    return build


def test_the_newest_entry_sorts_by_sequence_not_as_text():
    # The 999 -> 1000 rollover. As text `1000-` sorts between `100-` and
    # `101-`, so a text sort would freeze the marker at an old entry and the
    # card would read as permanently current -- the exact opposite of the
    # failure this whole feature is fixing.
    names = ["projects/x/100-cycle-90.md",
             "projects/x/1000-cycle-933.md",
             "projects/x/999-cycle-932.md"]
    assert recap_refresh.newest_entry(names) == "1000-cycle-933.md"


def test_no_entries_is_an_empty_marker_not_a_crash():
    assert recap_refresh.newest_entry([]) == ""
    assert recap_refresh.newest_entry(["projects/x/notes.txt"]) == ""


def test_an_unchanged_journal_costs_nothing(wired):
    entries = [entry(1, 900), entry(2, 901)]
    current = render(["- something"], journal="2-cycle-901.md")
    vault = wired(entries, recap=current, bullets=["a new bullet"])

    assert recap_refresh.refresh_once() is False
    assert vault.written == []
    # And it never even asked. That is the half his issue names as the
    # reason the marker is worth having at all.
    assert vault.asked == []


def test_a_new_entry_rewrites_the_card_and_restamps_it(wired):
    entries = [entry(1, 900), entry(2, 901), entry(3, 902)]
    stale = render(["old bullet"], journal="2-cycle-901.md")
    vault = wired(entries, recap=stale, bullets=["**New work** happened"])

    assert recap_refresh.refresh_once() is True

    (path, text), = vault.written
    assert path == RECAP_PATH
    payload = parse_recap(text)
    assert payload["journal"] == "3-cycle-902.md"
    assert [b["lead"] for b in payload["bullets"]] == ["New work"]


def test_a_card_with_no_marker_is_rebuilt_once_then_settles(wired):
    # Every card written before 2026-09-13 carries no `journal` marker. It
    # must be rebuilt (its marker can never match) and then stop.
    entries = [entry(1, 900)]
    legacy = render(["old bullet"], cycles="880-900")
    vault = wired(entries, recap=legacy, bullets=["**Rebuilt** it"])

    assert recap_refresh.refresh_once() is True
    assert recap_refresh.refresh_once() is False
    assert len(vault.written) == 1


def test_no_bullets_from_haiku_keeps_the_old_card(wired):
    entries = [entry(1, 900), entry(2, 901)]
    old = render(["the bullet he can still read"], journal="1-cycle-900.md")
    vault = wired(entries, recap=old, bullets=[])

    assert recap_refresh.refresh_once() is False
    assert vault.written == []
    assert vault.docs[RECAP_PATH] == old


def test_a_raising_bridge_keeps_the_old_card(wired):
    entries = [entry(1, 900), entry(2, 901)]
    old = render(["the bullet he can still read"], journal="1-cycle-900.md")
    vault = wired(entries, recap=old, raises=True)

    assert recap_refresh.refresh_once() is False
    assert vault.docs[RECAP_PATH] == old


def test_an_empty_journal_folder_writes_nothing(wired):
    vault = wired([], recap=render(["old"], journal="1-cycle-900.md"))
    assert recap_refresh.refresh_once() is False
    assert vault.written == []


def test_entries_that_will_not_read_do_not_stop_the_tick(wired):
    entries = [entry(1, 900), entry(2, 901)]
    vault = wired(entries, bullets=["**Still** wrote a card"])
    vault.docs[recap_refresh.JOURNAL_DIR + "1-cycle-900.md"] = None

    assert recap_refresh.refresh_once() is True
    assert "Cycle 901" in vault.asked[0]
    assert "Cycle 900" not in vault.asked[0]


def test_a_window_of_only_unreadable_entries_writes_nothing(wired):
    entries = [entry(1, 900)]
    vault = wired(entries, bullets=["would have been a card"])
    vault.docs[recap_refresh.JOURNAL_DIR + "1-cycle-900.md"] = ""

    assert recap_refresh.refresh_once() is False
    assert vault.written == []


def test_the_material_is_newest_first_and_capped_at_the_window():
    entries = dict(entry(seq, 900 + seq) for seq in range(1, 41))
    read = {recap_refresh.JOURNAL_DIR + name: body for name, body in entries.items()}
    rows = recap_refresh.raw_material(list(entries), read.get)

    assert len(rows) == recap_refresh.WINDOW_ENTRIES
    assert "Cycle 940" in rows[0]
    # 40 entries, newest 30 kept -> the oldest survivor is cycle 911.
    assert "Cycle 911" in rows[-1]


def test_the_material_carries_the_pr_line_so_a_bullet_can_link():
    name, body = entry(1, 900, pr="SokratesAI/marcus#142")
    rows = recap_refresh.raw_material(
        [name], {recap_refresh.JOURNAL_DIR + name: body}.get)
    assert "PR: SokratesAI/marcus#142" in rows[0]


def test_the_prompt_fences_the_entries_and_forbids_replying():
    rows = ["- Cycle 900 — Did a thing"]
    prompt = recap_refresh.prompt_for(rows)
    # The fence is what stops Haiku answering the material instead of
    # summarising it -- measured on the title lane, same bridge, same model.
    assert "<entries>" in prompt and "</entries>" in prompt
    assert "Do NOT reply" in prompt
    assert str(MAX_BULLETS) in prompt


def test_bullets_are_parsed_out_of_prose_around_them():
    text = ("Here is your summary:\n\n"
            "- **First** thing\n"
            "* **Second** thing\n"
            "\nLet me know if you want more detail.\n")
    assert recap_refresh.bullets_from(text) == ["**First** thing", "**Second** thing"]


def test_bullets_are_cut_to_his_ceiling():
    text = "\n".join(f"- bullet {n}" for n in range(MAX_BULLETS + 3))
    assert len(recap_refresh.bullets_from(text)) == MAX_BULLETS


def test_a_paragraph_with_a_dash_in_front_is_not_a_bullet():
    assert recap_refresh.bullets_from("- " + "x" * 401) == []


def test_output_with_no_bullets_at_all_is_empty():
    assert recap_refresh.bullets_from("I cannot help with that.") == []
    assert recap_refresh.bullets_from("") == []


def test_a_tick_arriving_mid_generation_is_dropped(wired):
    vault = wired([entry(1, 900)], bullets=["**A** card"])
    recap_refresh._lock.acquire()
    try:
        assert recap_refresh.refresh_once() is False
        assert vault.written == []
    finally:
        recap_refresh._lock.release()
