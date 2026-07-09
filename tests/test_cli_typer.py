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

    def status(self, json_output: bool = False) -> dict | None:
        self.calls.append(("status", {"json_output": json_output}))
        return {"schemaVersion": 1, "active": None} if json_output else None

    def add_account(self, slot: int | None = None, assume_yes: bool = False) -> None:
        self.calls.append(("add_account", {"slot": slot}))

    def add_account_from_token(
        self,
        token: str,
        email: str | None = None,
        slot: int | None = None,
        assume_yes: bool = False,
    ) -> None:
        self.calls.append(
            ("add_account_from_token", {"token": token, "email": email, "slot": slot})
        )

    def switch(self, strategy: str | None = None, json_output: bool = False) -> dict | None:
        self.calls.append(("switch", {"strategy": strategy, "json_output": json_output}))
        return {"schemaVersion": 1, "switched": True} if json_output else None

    def switch_to(
        self, identifier: str, json_output: bool = False, force: bool = False
    ) -> dict | None:
        self.calls.append(
            ("switch_to", {"identifier": identifier, "json_output": json_output, "force": force})
        )
        return {"schemaVersion": 1, "switched": True} if json_output else None

    def remove_account(self, identifier: str, assume_yes: bool = False) -> None:
        self.calls.append(("remove_account", {"identifier": identifier}))


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


def _last_call(stub: type[_StubSwitcher]) -> tuple[str, dict[str, object]]:
    assert stub.last is not None and stub.last.calls
    return stub.last.calls[-1]


def test_claude_list_is_claude_only(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["claude", "default", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schemaVersion"] == 1  # NOT the aggregate v2 envelope
    assert _last_call(stub_switcher) == (
        "list_accounts",
        {"show_token_status": False, "json_output": True},
    )


def test_claude_list_token_status_conflicts_with_json(
    stub_switcher: type[_StubSwitcher],
) -> None:
    result = runner.invoke(
        app, ["claude", "default", "list", "--json", "--token-status"]
    )
    assert result.exit_code == 2


def test_claude_status(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["claude", "default", "status"])
    assert result.exit_code == 0
    assert _last_call(stub_switcher) == ("status", {"json_output": False})


def test_claude_add_with_slot(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["claude", "default", "add", "--slot", "3"])
    assert result.exit_code == 0
    assert _last_call(stub_switcher) == ("add_account", {"slot": 3})


def test_claude_add_token(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(
        app,
        ["claude", "default", "add-token", "sk-tok", "--email", "me@x.com", "--slot", "2"],
    )
    assert result.exit_code == 0
    assert _last_call(stub_switcher) == (
        "add_account_from_token",
        {"token": "sk-tok", "email": "me@x.com", "slot": 2},
    )


def test_claude_bare_switch_rotates(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["claude", "default", "switch"])
    assert result.exit_code == 0
    assert _last_call(stub_switcher) == (
        "switch",
        {"strategy": None, "json_output": False},
    )


def test_claude_switch_with_strategy(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["claude", "default", "switch", "--strategy", "best"])
    assert result.exit_code == 0
    assert _last_call(stub_switcher) == (
        "switch",
        {"strategy": "best", "json_output": False},
    )


def test_claude_switch_rejects_unknown_strategy(
    stub_switcher: type[_StubSwitcher],
) -> None:
    result = runner.invoke(app, ["claude", "default", "switch", "--strategy", "bogus"])
    assert result.exit_code == 2  # enum choices enforced at the CLI boundary


def test_claude_switch_positional_target(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["claude", "default", "switch", "2"])
    assert result.exit_code == 0
    assert _last_call(stub_switcher) == (
        "switch_to",
        {"identifier": "2", "json_output": False, "force": False},
    )


def test_claude_switch_to_flag_and_force(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(
        app, ["claude", "default", "switch", "--to", "me@x.com", "--force"]
    )
    assert result.exit_code == 0
    assert _last_call(stub_switcher) == (
        "switch_to",
        {"identifier": "me@x.com", "json_output": False, "force": True},
    )


def test_claude_switch_rejects_both_positional_and_to(
    stub_switcher: type[_StubSwitcher],
) -> None:
    assert runner.invoke(app, ["claude", "default", "switch", "2", "--to", "3"]).exit_code == 2


def test_claude_switch_strategy_conflicts_with_target(
    stub_switcher: type[_StubSwitcher],
) -> None:
    result = runner.invoke(
        app, ["claude", "default", "switch", "2", "--strategy", "best"]
    )
    assert result.exit_code == 2


def test_claude_remove(stub_switcher: type[_StubSwitcher]) -> None:
    result = runner.invoke(app, ["claude", "default", "remove", "2"])
    assert result.exit_code == 0
    assert _last_call(stub_switcher) == ("remove_account", {"identifier": "2"})


def test_claude_export_and_import(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    def fake_export(switcher, destination, account=None, full=False):
        recorded["export"] = (destination, account, full)

    def fake_import(switcher, source, force=False):
        recorded["import"] = (source, force)

    monkeypatch.setattr("claude_swap.transfer.export_accounts", fake_export)
    monkeypatch.setattr("claude_swap.transfer.import_accounts", fake_import)

    assert runner.invoke(
        app, ["claude", "default", "export", "out.json", "--account", "2", "--full"]
    ).exit_code == 0
    assert recorded["export"] == ("out.json", "2", True)

    assert runner.invoke(
        app, ["claude", "default", "import", "in.json", "--force"]
    ).exit_code == 0
    assert recorded["import"] == ("in.json", True)


def test_claude_json_error_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_swap.exceptions import ConfigError

    class _BrokenSwitcher(_StubSwitcher):
        def status(self, json_output: bool = False) -> dict | None:
            raise ConfigError("boom")

    monkeypatch.setattr("claude_swap.cli.ClaudeAccountSwitcher", _BrokenSwitcher)
    result = runner.invoke(app, ["claude", "default", "status", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["message"] == "boom"


def test_run_forwards_args_after_double_dash(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    class _StubSession:
        def __init__(self, switcher: object) -> None:
            pass

        def run(
            self,
            identifier: str,
            claude_args: list[str],
            share: bool = True,
            share_history: bool = False,
        ) -> None:
            recorded["run"] = (identifier, claude_args, share, share_history)
            raise SystemExit(0)

    monkeypatch.setattr("claude_swap.session.SessionManager", _StubSession)
    result = runner.invoke(
        app,
        ["claude", "default", "run", "2", "--no-share", "--", "--resume", "-p", "hi"],
    )
    assert result.exit_code == 0
    assert recorded["run"] == ("2", ["--resume", "-p", "hi"], False, False)


def test_run_rejects_unknown_options_before_double_dash(
    stub_switcher: type[_StubSwitcher],
) -> None:
    result = runner.invoke(app, ["claude", "default", "run", "2", "--bogus-flag"])
    assert result.exit_code == 2


def test_auto_once_exit_code_reflects_tick_outcome(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import types

    _StubSwitcher.backup_dir_override = tmp_path
    captured: dict[str, object] = {}

    class _StubEngine:
        def __init__(self, switcher, settings, emit, dry_run=False) -> None:
            captured["settings"] = settings
            captured["dry_run"] = dry_run

        def tick(self) -> object:
            return types.SimpleNamespace(value=2)

    monkeypatch.setattr("claude_swap.autoswitch.AutoSwitchEngine", _StubEngine)
    result = runner.invoke(
        app, ["claude", "default", "auto", "--once", "--threshold", "80", "--dry-run"]
    )
    assert result.exit_code == 2
    assert captured["dry_run"] is True
    assert captured["settings"].threshold == 80.0
