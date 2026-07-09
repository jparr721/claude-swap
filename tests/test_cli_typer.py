"""Tests for the Typer command tree (drives `cli.app` via CliRunner)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_swap.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "claude_swap.update_check.check_for_update", lambda version: None
    )


class _StubSwitcher:
    """Signature-compatible stand-in recording every dispatched call."""

    last: "_StubSwitcher | None" = None
    backup_dir_override: Path | None = None

    def __init__(self, debug: bool = False) -> None:
        self.debug = debug
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.backup_dir = _StubSwitcher.backup_dir_override or Path(
            "/nonexistent-backup-root"
        )
        _StubSwitcher.last = self

    def _is_running_in_container(self) -> bool:
        return False

    def list_accounts(
        self, show_token_status: bool = False, json_output: bool = False
    ) -> dict | None:
        self.calls.append(
            ("list_accounts", {"show_token_status": show_token_status, "json_output": json_output})
        )
        if json_output:
            return {"schemaVersion": 1, "activeAccountNumber": None, "accounts": []}
        print("Accounts:")
        return None

    def purge(self) -> None:
        self.calls.append(("purge", {}))


@pytest.fixture
def stub_switcher(monkeypatch: pytest.MonkeyPatch) -> type[_StubSwitcher]:
    monkeypatch.setattr("claude_swap.cli.ClaudeAccountSwitcher", _StubSwitcher)
    monkeypatch.setattr("claude_swap.cli.managed_aggregate_providers", lambda: [])
    _StubSwitcher.last = None
    _StubSwitcher.backup_dir_override = None
    return _StubSwitcher


def test_ls_aggregates_with_schema_v2_envelope(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["ls", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schemaVersion"] == 2
    assert payload["providers"]["claude"]["default"]["accounts"] == []
    assert stub_switcher.last is not None
    assert stub_switcher.last.calls == [
        ("list_accounts", {"show_token_status": False, "json_output": True})
    ]


def test_ls_human_mode_prints_accounts(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "Accounts:" in result.stdout


def test_purge_routes_to_switcher(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["purge"])
    assert result.exit_code == 0
    assert stub_switcher.last.calls == [("purge", {})]


def test_upgrade_runs_self_upgrade_before_switcher_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {}

    def fake_upgrade() -> int:
        called["upgrade"] = True
        return 0

    monkeypatch.setattr("claude_swap.update_check.run_self_upgrade", fake_upgrade)

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("upgrade must not construct the switcher")

    monkeypatch.setattr("claude_swap.cli.ClaudeAccountSwitcher", explode)
    result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 0
    assert called == {"upgrade": True}


def test_update_is_an_alias_of_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_swap.update_check.run_self_upgrade", lambda: 0)
    assert runner.invoke(app, ["update"]).exit_code == 0


def test_version_flag() -> None:
    from claude_swap import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_set_and_get_round_trip(
    stub_switcher: type[_StubSwitcher], tmp_path: Path
) -> None:
    # Point the stub's backup root somewhere real so settings.json lands there.
    _StubSwitcher.backup_dir_override = tmp_path

    result = runner.invoke(app, ["config", "set", "autoswitch.threshold", "80"])
    assert result.exit_code == 0, result.output
    assert "autoswitch.threshold = 80" in result.stdout

    result = runner.invoke(app, ["config", "get", "autoswitch.threshold"])
    assert result.exit_code == 0
    assert "80" in result.stdout

    result = runner.invoke(app, ["config", "get", "autoswitch.threshold", "--json"])
    payload = json.loads(result.stdout)
    assert payload == {
        "schemaVersion": 1,
        "key": "autoswitch.threshold",
        "value": 80.0,
        "isSet": True,
    }


def test_bare_config_lists_settings(
    stub_switcher: type[_StubSwitcher], tmp_path: Path
) -> None:
    _StubSwitcher.backup_dir_override = tmp_path
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "autoswitch.threshold" in result.stdout


def test_group_level_json_flag_applies_to_get(
    stub_switcher: type[_StubSwitcher], tmp_path: Path
) -> None:
    _StubSwitcher.backup_dir_override = tmp_path
    result = runner.invoke(app, ["config", "--json", "get", "autoswitch.threshold"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["key"] == "autoswitch.threshold"
    assert payload["isSet"] is False
    assert "value" in payload


def test_group_level_json_flag_rejected_for_set(
    stub_switcher: type[_StubSwitcher], tmp_path: Path
) -> None:
    _StubSwitcher.backup_dir_override = tmp_path
    result = runner.invoke(
        app, ["config", "--json", "set", "autoswitch.threshold", "80"]
    )
    assert result.exit_code == 2


def test_post_verb_json_flag_still_works(
    stub_switcher: type[_StubSwitcher], tmp_path: Path
) -> None:
    _StubSwitcher.backup_dir_override = tmp_path
    result = runner.invoke(app, ["config", "get", "autoswitch.threshold", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["key"] == "autoswitch.threshold"
