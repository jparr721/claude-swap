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
    assert result.stderr == ""  # JSON mode keeps stderr clean (purity guarantee)


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


class _StubProviderStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_accounts(self, json_output: bool = False) -> dict | None:
        self.calls.append(("list_accounts", {"json_output": json_output}))
        if json_output:
            return {
                "schemaVersion": 1,
                "provider": {"frontend": "codex", "backend": "openai"},
                "activeAccountNumber": None,
                "accounts": [],
            }
        return None

    def status(self, json_output: bool = False) -> dict | None:
        self.calls.append(("status", {"json_output": json_output}))
        return {"schemaVersion": 1, "active": None} if json_output else None

    def add_account(self, label: str | None, slot: int | None) -> None:
        self.calls.append(("add_account", {"label": label, "slot": slot}))

    def switch(self, identifier: str | None, json_output: bool) -> dict | None:
        self.calls.append(("switch", {"identifier": identifier, "json_output": json_output}))
        return None

    def remove_account(self, identifier: str) -> None:
        self.calls.append(("remove_account", {"identifier": identifier}))


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {"store": _StubProviderStore()}

    def fake_get_provider(frontend: str, backend: str) -> _StubProviderStore:
        captured["ref"] = (frontend, backend)
        return captured["store"]

    monkeypatch.setattr("claude_swap.cli.get_provider", fake_get_provider)
    return captured


def test_codex_list_json(stub_provider: dict[str, object]) -> None:
    result = runner.invoke(app, ["codex", "openai", "list", "--json"])
    assert result.exit_code == 0
    assert stub_provider["ref"] == ("codex", "openai")
    assert stub_provider["store"].calls == [("list_accounts", {"json_output": True})]
    assert json.loads(result.stdout)["schemaVersion"] == 1


def test_codex_add_with_label_and_slot(stub_provider: dict[str, object]) -> None:
    result = runner.invoke(
        app, ["codex", "openai", "add", "--label", "work", "--slot", "2"]
    )
    assert result.exit_code == 0
    assert stub_provider["store"].calls == [("add_account", {"label": "work", "slot": 2})]


def test_codex_switch_positional_and_to_flag(stub_provider: dict[str, object]) -> None:
    assert runner.invoke(app, ["codex", "openai", "switch", "2"]).exit_code == 0
    assert runner.invoke(app, ["codex", "openai", "switch", "--to", "work"]).exit_code == 0
    assert runner.invoke(app, ["codex", "openai", "switch"]).exit_code == 0  # rotate
    assert stub_provider["store"].calls == [
        ("switch", {"identifier": "2", "json_output": False}),
        ("switch", {"identifier": "work", "json_output": False}),
        ("switch", {"identifier": None, "json_output": False}),
    ]


def test_codex_switch_rejects_both_positional_and_to(
    stub_provider: dict[str, object],
) -> None:
    result = runner.invoke(app, ["codex", "openai", "switch", "2", "--to", "work"])
    assert result.exit_code == 2


def test_codex_remove(stub_provider: dict[str, object]) -> None:
    assert runner.invoke(app, ["codex", "openai", "remove", "2"]).exit_code == 0
    assert stub_provider["store"].calls == [("remove_account", {"identifier": "2"})]


def test_opencode_switch_is_refused(temp_home: Path) -> None:
    # Real registry + real store: snapshot-refused providers error before
    # touching any account state. Note: on click versions that separate
    # stderr, check result.stderr instead of result.output for the message.
    result = runner.invoke(app, ["opencode", "openai", "switch", "1"])
    assert result.exit_code == 1
    assert "cannot safely restore" in result.output


def test_opencode_verbs_exist(stub_provider: dict[str, object]) -> None:
    result = runner.invoke(app, ["opencode", "openai", "list"])
    assert result.exit_code == 0
    assert stub_provider["ref"] == ("opencode", "openai")


# --------------------------------------------------------------------------
# Removal / contract assertions: the legacy argparse surface is gone. These
# pin the Typer tree's shape so a regression that reintroduces a top-level
# verb, a UI command, or a `--flag` interface fails loudly.
# --------------------------------------------------------------------------


def test_top_level_claude_verbs_are_removed() -> None:
    for verb in ("list", "status", "add", "add-token", "remove", "switch",
                 "export", "import", "run", "auto"):
        result = runner.invoke(app, [verb])
        assert result.exit_code == 2, f"top-level '{verb}' must not exist"


def test_ui_commands_are_removed() -> None:
    for verb in ("tui", "watch", "menubar"):
        assert runner.invoke(app, [verb]).exit_code == 2


def test_legacy_flags_are_removed() -> None:
    for flag in ("--list", "--status", "--switch", "--switch-to", "--add-account",
                 "--remove-account", "--export", "--import", "--purge", "--tui"):
        result = runner.invoke(app, [flag] if flag != "--switch-to" else [flag, "2"])
        assert result.exit_code == 2, f"legacy '{flag}' must not parse"


def test_bare_cswap_prints_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output
    assert "claude" in result.output and "codex" in result.output


def test_frontend_without_backend_shows_help() -> None:
    result = runner.invoke(app, ["codex"])
    assert "openai" in result.output


# --------------------------------------------------------------------------
# Ported behavioral tests from the retired argparse suite. These cover
# contract guarantees (error exit codes, JSON purity, aggregate-list
# resilience, provider-envelope nesting, auto loop/settings/JSONL) that the
# routing/flag tests above do not.
# --------------------------------------------------------------------------


def test_run_session_error_exits_cleanly(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ClaudeSwitchError during `run` exits 1 with the message on stderr."""
    from claude_swap.exceptions import SessionError

    class _FailingSession:
        def __init__(self, switcher: object) -> None:
            pass

        def run(
            self,
            identifier: str,
            claude_args: list[str],
            share: bool = True,
            share_history: bool = False,
        ) -> None:
            raise SessionError("boom")

    monkeypatch.setattr("claude_swap.session.SessionManager", _FailingSession)
    result = runner.invoke(app, ["claude", "default", "run", "2"])
    assert result.exit_code == 1
    assert "boom" in result.stderr


def test_ls_human_mode_appends_provider_accounts(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ls` renders provider sections after Claude when they have accounts."""
    from unittest.mock import MagicMock, call

    provider_store = MagicMock()
    provider_store.definition.ref.frontend = "codex"
    provider_store.definition.frontend.display_name = "Codex"
    provider_store.list_accounts.side_effect = [
        {"schemaVersion": 1, "provider": "codex", "accounts": [{"number": 1}]},
        None,
    ]
    monkeypatch.setattr(
        "claude_swap.cli.managed_aggregate_providers", lambda: [provider_store]
    )
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert provider_store.list_accounts.call_args_list == [
        call(json_output=True),
        call(json_output=False),
    ]


def test_ls_human_mode_survives_corrupt_provider_state(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken provider tree must not fail the primary Claude listing."""
    from unittest.mock import MagicMock

    from claude_swap.exceptions import ConfigError

    provider_store = MagicMock()
    provider_store.definition.frontend.display_name = "Codex"
    provider_store.list_accounts.side_effect = ConfigError(
        "Codex state file is not valid JSON"
    )
    monkeypatch.setattr(
        "claude_swap.cli.managed_aggregate_providers", lambda: [provider_store]
    )
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "Codex accounts unavailable" in result.stderr


def test_ls_json_nests_providers_by_frontend_and_backend(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    codex_payload = {
        "schemaVersion": 1,
        "provider": {"frontend": "codex", "backend": "openai"},
        "activeAccountNumber": 1,
        "accounts": [],
    }
    provider_store = Mock()
    provider_store.definition.ref.frontend = "codex"
    provider_store.definition.ref.backend = "openai"
    provider_store.list_accounts.return_value = codex_payload
    monkeypatch.setattr(
        "claude_swap.cli.managed_aggregate_providers", lambda: [provider_store]
    )
    result = runner.invoke(app, ["ls", "--json"])
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["schemaVersion"] == 2
    assert out["providers"]["claude"]["default"]["accounts"] == []
    assert out["providers"]["codex"]["openai"]["accounts"] == []
    assert "accounts" not in out  # flat legacy keys are gone
    assert "codex" not in out


def test_ls_json_nests_provider_errors(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from claude_swap.exceptions import ConfigError

    provider_store = Mock()
    provider_store.definition.ref.frontend = "codex"
    provider_store.definition.ref.backend = "openai"
    provider_store.list_accounts.side_effect = ConfigError(
        "Codex state file is not valid JSON"
    )
    monkeypatch.setattr(
        "claude_swap.cli.managed_aggregate_providers", lambda: [provider_store]
    )
    result = runner.invoke(app, ["ls", "--json"])
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["providers"]["codex"]["openai"] == {
        "error": {
            "type": "ConfigError",
            "message": "Codex state file is not valid JSON",
        }
    }


def test_auto_loop_mode_returns_loop_exit(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _StubSwitcher.backup_dir_override = tmp_path
    instances: list[object] = []

    class _LoopEngine:
        def __init__(self, switcher, settings, emit, dry_run=False) -> None:
            instances.append(self)

        def run_loop(self) -> int:
            return 0

        def stop(self) -> None:
            pass

    monkeypatch.setattr("claude_swap.autoswitch.AutoSwitchEngine", _LoopEngine)
    result = runner.invoke(app, ["claude", "default", "auto"])
    assert result.exit_code == 0
    assert instances  # the loop path constructed the engine


def test_auto_flags_override_settings_file(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI flags win; unspecified keys keep their settings.json values."""
    import types

    _StubSwitcher.backup_dir_override = tmp_path
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "autoswitch": {"threshold": 80.0, "cooldownSeconds": 42.0},
            }
        )
    )
    captured: dict[str, object] = {}

    class _CaptureEngine:
        def __init__(self, switcher, settings, emit, dry_run=False) -> None:
            captured["settings"] = settings

        def tick(self) -> object:
            return types.SimpleNamespace(value=2)

    monkeypatch.setattr("claude_swap.autoswitch.AutoSwitchEngine", _CaptureEngine)
    result = runner.invoke(
        app, ["claude", "default", "auto", "--once", "--threshold", "60"]
    )
    assert result.exit_code == 2
    assert captured["settings"].threshold == 60.0        # CLI wins
    assert captured["settings"].cooldown_seconds == 42.0  # settings.json kept


def test_auto_json_stdout_is_pure_jsonl(
    stub_switcher: type[_StubSwitcher], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import types

    from claude_swap.autoswitch import NoSwitchEvent

    _StubSwitcher.backup_dir_override = tmp_path

    class _EmittingEngine:
        def __init__(self, switcher, settings, emit, dry_run=False) -> None:
            self.emit = emit

        def tick(self) -> object:
            self.emit(NoSwitchEvent(reason="below-threshold"))
            self.emit(NoSwitchEvent(reason="cooldown"))
            return types.SimpleNamespace(value=2)

    monkeypatch.setattr("claude_swap.autoswitch.AutoSwitchEngine", _EmittingEngine)
    result = runner.invoke(app, ["claude", "default", "auto", "--once", "--json"])
    assert result.exit_code == 2
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        payload = json.loads(line)
        assert payload["event"] == "no-switch"
        assert payload["schemaVersion"] == 1


def test_auto_switcher_error_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_swap.exceptions import ConfigError

    def boom(debug: bool = False) -> None:
        raise ConfigError("nope")

    monkeypatch.setattr("claude_swap.cli.ClaudeAccountSwitcher", boom)
    result = runner.invoke(app, ["claude", "default", "auto", "--once"])
    assert result.exit_code == 1
    assert "nope" in result.stderr
