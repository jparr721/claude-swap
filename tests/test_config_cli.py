"""Tests for the `cswap config` subcommand (get/set/unset/list/path).

Drives the Typer `config` sub-app through `CliRunner` against a real switcher
under an isolated `temp_home`, so the settings.json read/write, validation
messages, and JSON shapes are exercised end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_swap.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _nonroot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The root guard calls os.geteuid(); pretend we are an ordinary user so
    the suite passes even when run as root (e.g. inside a CI container)."""
    monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run `cswap config <argv>`; returns (exit_code, stdout, stderr)."""
    result = runner.invoke(app, ["config", *argv])
    return result.exit_code, result.stdout, result.stderr


def _settings_file() -> Path:
    code, out, _ = _run(["path"])
    assert code == 0
    return Path(out.strip())


class TestConfigList:
    def test_lists_all_keys_as_defaults(self, temp_home):
        code, out, _ = _run([])
        assert code == 0
        for key in (
            "autoswitch.threshold",
            "autoswitch.intervalSeconds",
            "autoswitch.cooldownSeconds",
            "autoswitch.hysteresisPct",
            "autoswitch.strategy",
            "autoswitch.includeApiKeyAccounts",
            "autoswitch.unhealthyTicks",
        ):
            assert key in out
        assert out.count("(default)") == 7

    def test_set_key_not_marked_default(self, temp_home):
        _run(["set", "autoswitch.cooldownSeconds", "600"])
        code, out, _ = _run([])
        assert code == 0
        cooldown_line = next(
            ln for ln in out.splitlines() if "cooldownSeconds" in ln
        )
        assert "600" in cooldown_line
        assert "(default)" not in cooldown_line

    def test_set_equal_to_default_still_counts_as_set(self, temp_home):
        _run(["set", "autoswitch.threshold", "90"])
        _, out, _ = _run([])
        threshold_line = next(
            ln for ln in out.splitlines() if "threshold" in ln
        )
        assert "(default)" not in threshold_line

    def test_json_list(self, temp_home):
        _run(["set", "autoswitch.threshold", "90"])
        code, out, _ = _run(["--json"])
        assert code == 0
        payload = json.loads(out)
        assert payload["schemaVersion"] == 1
        assert payload["path"].endswith("settings.json")
        by_key = {entry["key"]: entry for entry in payload["settings"]}
        assert len(by_key) == 7
        assert by_key["autoswitch.threshold"]["value"] == 90.0
        assert by_key["autoswitch.threshold"]["isSet"] is True
        assert by_key["autoswitch.includeApiKeyAccounts"]["value"] is False


class TestConfigSetGet:
    def test_set_then_get(self, temp_home):
        code, out, _ = _run(["set", "autoswitch.threshold", "80"])
        assert code == 0
        assert "autoswitch.threshold = 80" in out
        code, out, _ = _run(["get", "autoswitch.threshold"])
        assert code == 0
        assert out.strip() == "80"

    def test_set_writes_only_that_key(self, temp_home):
        """The trap guard: no other defaults get materialized into the file."""
        _run(["set", "autoswitch.threshold", "80"])
        raw = json.loads(_settings_file().read_text())
        assert set(raw) == {"schemaVersion", "autoswitch"}
        assert set(raw["autoswitch"]) == {"threshold"}
        assert raw["autoswitch"]["threshold"] == 80.0

    def test_set_bool_words(self, temp_home):
        code, out, _ = _run(["set", "autoswitch.includeApiKeyAccounts", "no"])
        assert code == 0
        assert "= false" in out
        raw = json.loads(_settings_file().read_text())
        assert raw["autoswitch"]["includeApiKeyAccounts"] is False

    def test_set_preserves_unknown_keys(self, temp_home):
        path = _settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schemaVersion": 1,
            "futureSection": {"x": 1},
            "autoswitch": {"threshold": 80, "futureKnob": True},
        }))
        code, _, _ = _run(["set", "autoswitch.threshold", "70"])
        assert code == 0
        raw = json.loads(path.read_text())
        assert raw["futureSection"] == {"x": 1}
        assert raw["autoswitch"]["futureKnob"] is True
        assert raw["autoswitch"]["threshold"] == 70.0

    def test_get_json_trailing_and_leading_flag(self, temp_home):
        _run(["set", "autoswitch.threshold", "80"])
        for argv in (
            ["get", "autoswitch.threshold", "--json"],
            ["--json", "get", "autoswitch.threshold"],
        ):
            code, out, _ = _run(argv)
            assert code == 0
            payload = json.loads(out)
            assert payload == {
                "schemaVersion": 1,
                "key": "autoswitch.threshold",
                "value": 80.0,
                "isSet": True,
            }


class TestConfigValidation:
    def test_out_of_range_exits_1(self, temp_home):
        code, _, err = _run(["set", "autoswitch.threshold", "30"])
        assert code == 1
        assert "between 50 and 99.9" in err

    def test_unknown_key_exits_1_and_lists_valid_keys(self, temp_home):
        code, _, err = _run(["set", "autoswitch.bogus", "1"])
        assert code == 1
        assert "unknown setting" in err
        assert "autoswitch.threshold" in err

    def test_bad_bool_exits_1(self, temp_home):
        code, _, err = _run(["set", "autoswitch.includeApiKeyAccounts", "falsy"])
        assert code == 1
        assert "true or false" in err

    def test_bad_number_exits_1(self, temp_home):
        code, _, err = _run(["set", "autoswitch.threshold", "high"])
        assert code == 1
        assert "expects a number" in err

    def test_int_key_rejects_float(self, temp_home):
        code, _, err = _run(["set", "autoswitch.unhealthyTicks", "3.5"])
        assert code == 1
        assert "expects an integer" in err

    def test_bad_strategy_exits_1(self, temp_home):
        code, _, err = _run(["set", "autoswitch.strategy", "chaos"])
        assert code == 1
        assert "must be one of: best" in err

    def test_unknown_key_json_error_envelope(self, temp_home):
        code, out, _ = _run(["--json", "get", "autoswitch.bogus"])
        assert code == 1
        payload = json.loads(out)
        assert payload["schemaVersion"] == 1
        assert "unknown setting" in payload["error"]["message"]

    def test_corrupt_file_set_exits_1_and_leaves_file_untouched(self, temp_home):
        path = _settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        code, _, err = _run(["set", "autoswitch.threshold", "80"])
        assert code == 1
        assert "not valid JSON" in err
        assert path.read_text() == "{not json"

    def test_missing_value_usage_error_exits_2(self, temp_home):
        code, _, _ = _run(["set", "autoswitch.threshold"])
        assert code == 2

    def test_unknown_action_exits_2(self, temp_home):
        code, _, _ = _run(["frobnicate"])
        assert code == 2

    def test_json_with_set_rejected(self, temp_home):
        code, _, _ = _run(["--json", "set", "autoswitch.threshold", "80"])
        assert code == 2


class TestConfigUnset:
    def test_unset_restores_default(self, temp_home):
        _run(["set", "autoswitch.threshold", "80"])
        code, out, _ = _run(["unset", "autoswitch.threshold"])
        assert code == 0
        assert "default: 90" in out
        code, out, _ = _run(["get", "autoswitch.threshold"])
        assert out.strip() == "90"
        # The emptied autoswitch section is removed entirely.
        raw = json.loads(_settings_file().read_text())
        assert "autoswitch" not in raw

    def test_unset_when_not_set_is_a_noop(self, temp_home):
        code, _, err = _run(["unset", "autoswitch.threshold"])
        assert code == 0
        assert "not set" in err


class TestConfigMisc:
    def test_path_prints_settings_location(self, temp_home):
        code, out, _ = _run(["path"])
        assert code == 0
        assert out.strip().endswith("settings.json")

    def test_config_help(self, temp_home):
        code, out, _ = _run(["--help"])
        assert code == 0
        # Typer lists the subcommands rather than argparse's key epilog.
        for verb in ("list", "get", "set", "unset", "path"):
            assert verb in out

    def test_main_help_mentions_config(self, temp_home):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "config" in result.output

    def test_auto_picks_up_configured_threshold(self, temp_home, monkeypatch):
        """End-to-end: a value set via config drives `cswap ... auto`."""
        _run(["set", "autoswitch.threshold", "77"])

        captured = {}

        class FakeEngine:
            def __init__(self, switcher, settings, on_event, *, dry_run=False,
                         state_path=None, clock=None):
                captured["settings"] = settings

            def tick(self):
                from claude_swap.autoswitch import TickOutcome

                return TickOutcome.NO_ACTION

        monkeypatch.setattr("claude_swap.autoswitch.AutoSwitchEngine", FakeEngine)
        runner.invoke(app, ["claude", "auto", "--once"])
        assert captured["settings"].threshold == 77.0
