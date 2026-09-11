"""Per-conversation Settings: mute and answer style, stored as Agora tags.

His ask, 2026-09-11. The tags are shared with Agora (`/notify` withholds a
push for `nova:mute`) and the runner (`build_system` reads `nova:style=`), so
these pin the spellings as well as the read-modify-write.
"""
from unittest.mock import patch

import agora_runner.nova_conversations as convs
from agora_runner.turns import STYLE_TAG_PREFIX as RUNNER_STYLE_PREFIX


def _store(tags):
    state = {"tags": list(tags), "patched": []}

    def get(path):
        assert path.endswith("/messages?limit=1"), path
        return 200, {"id": "c-1", "tags": list(state["tags"]), "messages": []}

    def internal(method, path, payload=None):
        assert method == "PATCH"
        state["patched"].append(payload)
        state["tags"] = list(payload["tags"])
        return 200, {}
    return state, get, internal


def test_muting_keeps_every_other_tag():
    """Agora's PATCH replaces `tags` wholesale. Writing only the mute tag
    would erase `evolve-cycle:`, which is how a cycle thread is found."""
    state, get, internal = _store(["evolve-cycle:1200", "nova:style=brief"])
    with patch.object(convs, "agora_get", side_effect=get), \
            patch.object(convs, "agora_internal", side_effect=internal):
        assert convs.set_mute("c-1", "on") == (True, "on")
    assert set(state["tags"]) == {"evolve-cycle:1200", "nova:style=brief", "nova:mute"}
    with patch.object(convs, "agora_get", side_effect=get), \
            patch.object(convs, "agora_internal", side_effect=internal):
        assert convs.set_mute("c-1", "off") == (True, "off")
    assert set(state["tags"]) == {"evolve-cycle:1200", "nova:style=brief"}


def test_detailed_is_a_tag_and_brief_is_its_absence():
    """Brief is the default (his call, 2026-09-11), so switching back to it
    removes the tag -- one way to be Brief, not two."""
    state, get, internal = _store(["evolve-cycle:1200"])
    with patch.object(convs, "agora_get", side_effect=get), \
            patch.object(convs, "agora_internal", side_effect=internal):
        assert convs.set_style("c-1", "detailed") == (True, "detailed")
        assert state["tags"] == ["evolve-cycle:1200", "nova:style=detailed"]
        assert convs.set_style("c-1", "brief") == (True, "brief")
    assert state["tags"] == ["evolve-cycle:1200"]


def test_an_untouched_thread_reads_as_brief():
    _state, get, _internal = _store(["evolve-cycle:1200"])
    with patch.object(convs, "agora_get", side_effect=get):
        assert convs.prefs("c-1") == (True, {"muted": False, "style": "brief"})


def test_prefs_reads_mute_and_style_off_the_tags():
    _state, get, _internal = _store(["nova:mute", "nova:style=detailed", "nova:style=bogus"])
    with patch.object(convs, "agora_get", side_effect=get):
        assert convs.prefs("c-1") == (True, {"muted": True, "style": "detailed"})


def test_bad_values_are_refused_before_anything_is_written():
    with patch.object(convs, "agora_internal") as internal:
        assert convs.set_mute("c-1", "yes")[0] is False
        assert convs.set_style("c-1", "shouty")[0] is False
        assert convs.set_style("c-1", "")[0] is False
    internal.assert_not_called()


def test_the_style_tag_is_spelled_the_way_the_runner_reads_it():
    """Settings writes it, `build_system` reads it -- in two modules."""
    assert convs.STYLE_TAG_PREFIX == RUNNER_STYLE_PREFIX
