import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from tools import cli_features


WATCHED = (
    {
        "name": "auto-dream",
        "gate": "tengu_onyx_plover",
        "setting": "autoDreamEnabled",
        "why": "idea #83",
        "action": "flip the settings key",
    },
)


def write_config(gates, cached_at):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    payload = {}
    if gates is not None:
        payload["cachedGrowthBookFeatures"] = gates
    if cached_at is not None:
        payload["cachedGrowthBookFeaturesAt"] = cached_at.timestamp() * 1000
    json.dump(payload, handle)
    handle.close()
    return handle.name


class GateOpenTests(unittest.TestCase):
    """The rule is copied from the binary: enabled OR available."""

    def test_object_with_enabled_true_is_open(self):
        self.assertTrue(cli_features.gate_open({"enabled": True}))

    def test_object_with_available_true_is_open(self):
        self.assertTrue(cli_features.gate_open({"available": True, "enabled": False}))

    def test_bare_true_is_open(self):
        self.assertTrue(cli_features.gate_open(True))

    def test_the_shape_this_account_actually_carries_is_closed(self):
        # Measured on 2026-08-27: the live value for tengu_onyx_plover.
        # remoteEnabled is a different key and must not be read as the answer.
        self.assertFalse(
            cli_features.gate_open(
                {"enabled": False, "minHours": 24, "minSessions": 3, "remoteEnabled": False}
            )
        )

    def test_remote_enabled_alone_does_not_open_the_gate(self):
        self.assertFalse(cli_features.gate_open({"enabled": False, "remoteEnabled": True}))

    def test_falsey_shapes_are_closed(self):
        for value in (False, None, {}, "true", 1):
            self.assertFalse(cli_features.gate_open(value), value)


class JudgeTests(unittest.TestCase):
    def test_open_gate_is_open(self):
        verdicts = cli_features.judge({"tengu_onyx_plover": {"enabled": True}}, WATCHED)
        self.assertEqual([v["state"] for v in verdicts], ["open"])

    def test_closed_gate_is_closed(self):
        verdicts = cli_features.judge({"tengu_onyx_plover": {"enabled": False}}, WATCHED)
        self.assertEqual([v["state"] for v in verdicts], ["closed"])

    def test_a_gate_this_account_has_never_seen_is_unknown_not_closed(self):
        verdicts = cli_features.judge({"tengu_something_else": True}, WATCHED)
        self.assertEqual([v["state"] for v in verdicts], ["unknown"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.cached_at = datetime(2026, 8, 27, 10, 2, 25, tzinfo=timezone.utc)
        self.age = timedelta(minutes=8)

    def render(self, gates):
        out = io.StringIO()
        status = cli_features.report(
            cli_features.judge(gates, WATCHED), self.cached_at, self.age, out=out
        )
        return status, out.getvalue()

    def test_open_gate_exits_2_and_names_the_action(self):
        status, text = self.render({"tengu_onyx_plover": {"enabled": True}})
        self.assertEqual(status, 2)
        self.assertIn("GATE OPEN", text)
        self.assertIn("flip the settings key", text)

    def test_closed_gate_exits_0(self):
        status, text = self.render({"tengu_onyx_plover": {"enabled": False}})
        self.assertEqual(status, 0)
        self.assertNotIn("GATE OPEN", text)
        self.assertIn("closed", text)

    def test_unknown_gate_exits_1_and_says_it_is_not_closed(self):
        status, text = self.render({})
        self.assertEqual(status, 1)
        self.assertIn("CANNOT SEE", text)
        self.assertIn("not closed", text)

    def test_the_report_names_what_it_swept_and_when(self):
        _, text = self.render({"tengu_onyx_plover": {"enabled": False}})
        self.assertIn("Read 1 watched", text)
        self.assertIn("2026-08-27T10:02:25", text)


class MainTests(unittest.TestCase):
    def tearDown(self):
        for path in getattr(self, "_paths", []):
            os.unlink(path)

    def config(self, gates, cached_at):
        path = write_config(gates, cached_at)
        self._paths = getattr(self, "_paths", []) + [path]
        return path

    def test_fresh_closed_gate_exits_0(self):
        path = self.config(
            {"tengu_onyx_plover": {"enabled": False}}, datetime.now(timezone.utc)
        )
        self.assertEqual(cli_features.main(["--config", path]), 0)

    def test_fresh_open_gate_exits_2(self):
        path = self.config(
            {"tengu_onyx_plover": {"enabled": True}}, datetime.now(timezone.utc)
        )
        self.assertEqual(cli_features.main(["--config", path]), 2)

    def test_a_stale_cache_exits_1_rather_than_reporting_the_gate(self):
        # The failure this guards: a cache written before the gate flipped
        # reads exactly like a gate that is still shut.
        path = self.config(
            {"tengu_onyx_plover": {"enabled": True}},
            datetime.now(timezone.utc) - timedelta(hours=48),
        )
        self.assertEqual(cli_features.main(["--config", path]), 1)

    def test_a_stale_cache_inside_the_window_is_still_read(self):
        path = self.config(
            {"tengu_onyx_plover": {"enabled": True}},
            datetime.now(timezone.utc) - timedelta(hours=48),
        )
        self.assertEqual(cli_features.main(["--config", path, "--max-age-hours", "72"]), 2)

    def test_missing_file_exits_1(self):
        self.assertEqual(cli_features.main(["--config", "/nonexistent/.claude.json"]), 1)

    def test_config_without_the_gate_blob_exits_1(self):
        path = self.config(None, datetime.now(timezone.utc))
        self.assertEqual(cli_features.main(["--config", path]), 1)

    def test_config_without_a_timestamp_exits_1(self):
        path = self.config({"tengu_onyx_plover": True}, None)
        self.assertEqual(cli_features.main(["--config", path]), 1)


class ConfigPathTests(unittest.TestCase):
    def test_claude_config_dir_wins_when_set(self):
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/somewhere"
        try:
            self.assertEqual(cli_features.config_path(), "/tmp/somewhere/.claude.json")
        finally:
            if old is None:
                del os.environ["CLAUDE_CONFIG_DIR"]
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_falls_back_to_the_home_claude_directory(self):
        old = os.environ.pop("CLAUDE_CONFIG_DIR", None)
        try:
            self.assertTrue(cli_features.config_path().endswith("/.claude/.claude.json"))
        finally:
            if old is not None:
                os.environ["CLAUDE_CONFIG_DIR"] = old


class LiveTests(unittest.TestCase):
    """Against this pod's real config, when there is one."""

    def test_the_real_cache_parses_and_answers(self):
        path = cli_features.config_path()
        if not os.path.exists(path):
            self.skipTest("no CLI config on this box")
        gates, cached_at = cli_features.read_gates(path)
        self.assertGreater(len(gates), 100)
        self.assertEqual(cached_at.tzinfo, timezone.utc)
        # Every watched gate must classify without raising.
        states = {v["state"] for v in cli_features.judge(gates)}
        self.assertTrue(states <= {"open", "closed", "unknown"})


if __name__ == "__main__":
    unittest.main()
