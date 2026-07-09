from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_swap.exceptions import ConfigError
from claude_swap.paths import get_backup_root, get_provider_store_root
from claude_swap.providers.frontends import CodexFrontend, OpencodeFrontend
from claude_swap.providers.openai import CodexOpenAIBackend, OpencodeOpenAIBackend
from claude_swap.providers.store import ProviderAccountStore
from claude_swap.providers.types import ProviderDefinition, ProviderRef


def _codex_auth(account_id: str) -> dict[str, object]:
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "account_id": account_id,
            "access_token": f"token-{account_id}",
        },
    }


def _opencode_auth(account_id: str) -> dict[str, object]:
    return {
        "openai": {
            "type": "oauth",
            "access": f"token-{account_id}",
            "refresh": f"refresh-{account_id}",
            "expires": 1784223299464,
            "accountId": account_id,
        }
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _codex_store() -> ProviderAccountStore:
    ref = ProviderRef("codex", "openai")
    return ProviderAccountStore(
        ProviderDefinition(
            ref=ref,
            frontend=CodexFrontend(),
            backend=CodexOpenAIBackend(),
            state_dir=get_provider_store_root(ref.frontend, ref.backend),
            default_label_prefix="codex-openai-account",
            switch_mode="symlink",
        )
    )


def _opencode_store() -> ProviderAccountStore:
    ref = ProviderRef("opencode", "openai")
    return ProviderAccountStore(
        ProviderDefinition(
            ref=ref,
            frontend=OpencodeFrontend(),
            backend=OpencodeOpenAIBackend(),
            state_dir=get_provider_store_root(ref.frontend, ref.backend),
            default_label_prefix="opencode-openai-account",
            switch_mode="snapshot-refused",
        )
    )


def test_codex_store_adds_and_lists_account(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_payload = _codex_auth("acct-1")
    store = _codex_store()
    backend_calls: list[tuple[str, float]] = []

    def _fake_fetch_usage(auth_text: str, timeout_s: float) -> dict[str, object]:
        backend_calls.append((auth_text, timeout_s))
        return {"windows": [{"label": "Monthly", "pct": 12.0}]}

    monkeypatch.setattr(store.definition.backend, "fetch_usage", _fake_fetch_usage)

    def fake_login() -> None:
        store.auth_path.write_text(json.dumps(auth_payload), encoding="utf-8")

    monkeypatch.setattr(store, "_run_headless_login", fake_login)

    store.add_account(label="work", slot=1)
    payload = store.list_accounts(json_output=True)

    assert backend_calls == [(json.dumps(auth_payload), 10.0)]
    assert payload["schemaVersion"] == 1
    assert payload["provider"] == {"frontend": "codex", "backend": "openai"}
    assert payload["activeAccountNumber"] == 1
    assert payload["accounts"][0]["label"] == "work"
    assert payload["accounts"][0]["active"] is True


def test_opencode_store_refuses_to_restore_openai_oauth_snapshot(temp_home: Path) -> None:
    auth_path = temp_home / ".local" / "share" / "opencode" / "auth.json"
    _write(auth_path, _opencode_auth("acct-1"))
    store = _opencode_store()
    store.add_account(label="one", slot=1)
    _write(auth_path, _opencode_auth("acct-2"))
    store.add_account(label="two", slot=2)

    with pytest.raises(ConfigError, match="cannot safely restore stored OpenAI OAuth"):
        store.switch("1", json_output=False)

    assert json.loads(auth_path.read_text(encoding="utf-8"))["openai"]["accountId"] == "acct-2"


def test_codex_store_switches_by_symlink_rotation(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    for num, acct in (("1", "acct-1"), ("2", "acct-2")):
        _write(store._auth_backup_path(num), _codex_auth(acct))
        store._set_account_record(data, num, f"a{num}", store._metadata(json.dumps(_codex_auth(acct))))
    store._write_json(store.sequence_file, data)
    store._activate_auth_symlink(store._auth_backup_path("1"))

    result = store.switch(None, json_output=True)  # rotate to next

    assert result["switched"] is True
    assert result["to"]["number"] == 2
    assert store.auth_path.resolve() == store._auth_backup_path("2").resolve()


def test_missing_active_auth_mentions_provider_login(temp_home: Path) -> None:
    store = _opencode_store()

    with pytest.raises(ConfigError, match="opencode auth"):
        store.add_account(label=None, slot=None)


def test_codex_provider_store_ignores_existing_codex_backup(temp_home: Path) -> None:
    backup_root = get_backup_root()
    legacy = backup_root / "codex"
    legacy_auth = legacy / "auth"
    legacy_auth.mkdir(parents=True)
    (legacy / "sequence.json").write_text(
        json.dumps(
            {
                "activeAccountNumber": 1,
                "lastUpdated": "2026-07-08T00:00:00Z",
                "sequence": [1],
                "accounts": {
                    "1": {
                        "label": "work",
                        "accountId": "acct-1",
                        "authMode": "chatgpt",
                        "fingerprint": "abc",
                        "added": "2026-07-08T00:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (legacy_auth / "account-1.json").write_text("{}", encoding="utf-8")

    store = _codex_store()
    payload = store.list_accounts(json_output=True)

    assert not store.sequence_file.exists()
    assert not (store.auth_dir / "account-1.json").exists()
    assert payload["accounts"] == []


def test_provider_store_rejects_nonnumeric_account_keys(temp_home: Path) -> None:
    store = _codex_store()
    store.sequence_file.parent.mkdir(parents=True)
    store.sequence_file.write_text(
        json.dumps(
            {
                "activeAccountNumber": None,
                "lastUpdated": "2026-07-08T00:00:00Z",
                "sequence": [],
                "accounts": {"work": {"label": "work"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="state account keys must be numeric"):
        store.list_accounts(json_output=True)


def test_openai_switch_refuses_before_stored_auth_lookup(temp_home: Path) -> None:
    store = _opencode_store()
    store.sequence_file.parent.mkdir(parents=True)
    store.sequence_file.write_text(
        json.dumps(
            {
                "activeAccountNumber": None,
                "lastUpdated": "2026-07-08T00:00:00Z",
                "sequence": [1],
                "accounts": {"1": {"label": "work"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="cannot safely restore stored OpenAI OAuth"):
        store.switch("1", json_output=False)


def test_activate_auth_symlink_points_active_at_target(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    target = store._auth_backup_path("1")
    _write(target, _codex_auth("acct-1"))

    store._activate_auth_symlink(target)

    assert store.auth_path.is_symlink()
    assert store.auth_path.resolve() == target.resolve()
    assert store._active_symlink_target().resolve() == target.resolve()


def test_activate_auth_symlink_replaces_existing_real_file(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    _write(store.auth_path, _codex_auth("real-live"))  # a real file, not a symlink
    target = store._auth_backup_path("2")
    _write(target, _codex_auth("acct-2"))

    store._activate_auth_symlink(target)

    assert store.auth_path.is_symlink()
    assert store.auth_path.resolve() == target.resolve()


def test_codex_writethrough_symlink_updates_target(temp_home: Path) -> None:
    # Core invariant: writing to auth.json (as Codex does, in place) lands on the
    # per-account target file, so rotation stays in the account file.
    store = _codex_store()
    store._setup_directories()
    target = store._auth_backup_path("1")
    _write(target, _codex_auth("acct-1"))
    store._activate_auth_symlink(target)

    rotated = {"auth_mode": "chatgpt", "tokens": {"account_id": "acct-1", "access_token": "ROTATED"}}
    store.auth_path.write_text(json.dumps(rotated), encoding="utf-8")  # simulate Codex refresh

    assert store.auth_path.is_symlink()  # write followed the symlink; it stays a symlink
    assert '"ROTATED"' in target.read_text(encoding="utf-8")


def test_adopt_active_real_file_preserves_managed_account(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    old = {"auth_mode": "chatgpt", "tokens": {"account_id": "acct-1", "access_token": "OLD"}}
    fresh = {"auth_mode": "chatgpt", "tokens": {"account_id": "acct-1", "access_token": "FRESH"}}
    _write(store._auth_backup_path("1"), old)  # stored (stale) target
    data = store._sequence_data()
    store._set_account_record(data, "1", "one", store._metadata(json.dumps(old)))
    store._write_json(store.sequence_file, data)
    _write(store.auth_path, fresh)  # live real auth.json = fresher acct-1 creds

    store._adopt_active_real_file(store._sequence_data())

    assert '"FRESH"' in store._auth_backup_path("1").read_text(encoding="utf-8")


def test_adopt_active_real_file_rejects_unmanaged(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    _write(store.auth_path, _codex_auth("stranger"))

    with pytest.raises(ConfigError, match="not a managed"):
        store._adopt_active_real_file(store._sequence_data())


def test_adopt_active_real_file_noop_when_symlink(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    target = store._auth_backup_path("1")
    _write(target, _codex_auth("acct-1"))
    store._activate_auth_symlink(target)

    store._adopt_active_real_file(store._sequence_data())  # must not raise


def test_codex_switch_repoints_symlink_without_touching_bytes(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    for num, acct in (("1", "acct-1"), ("2", "acct-2")):
        _write(store._auth_backup_path(num), _codex_auth(acct))
        store._set_account_record(data, num, f"a{num}", store._metadata(json.dumps(_codex_auth(acct))))
    store._write_json(store.sequence_file, data)
    store._activate_auth_symlink(store._auth_backup_path("1"))

    store.switch("2", json_output=False)

    assert store.auth_path.is_symlink()
    assert store.auth_path.resolve() == store._auth_backup_path("2").resolve()
    # account-1 target bytes are untouched (no snapshot write)
    assert '"acct-1"' in store._auth_backup_path("1").read_text(encoding="utf-8")


def test_codex_switch_to_missing_credential_tells_user_to_readd(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    store._set_account_record(data, "1", "one", store._metadata(json.dumps(_codex_auth("acct-1"))))
    store._write_json(store.sequence_file, data)  # registered but NO target file on disk

    with pytest.raises(ConfigError, match="add --slot 1"):
        store.switch("1", json_output=False)


def test_opencode_switch_still_refused(temp_home: Path) -> None:
    store = _opencode_store()
    with pytest.raises(ConfigError, match="cannot safely restore stored OpenAI OAuth"):
        store.switch("1", json_output=False)


def test_current_account_number_reads_symlink_target(temp_home: Path) -> None:
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    _write(store._auth_backup_path("2"), _codex_auth("acct-2"))
    store._set_account_record(data, "2", "two", store._metadata(json.dumps(_codex_auth("acct-2"))))
    store._write_json(store.sequence_file, data)
    store._activate_auth_symlink(store._auth_backup_path("2"))

    assert store._current_account_number(store._sequence_data(), None) == "2"


def test_codex_add_runs_device_login_then_registers(temp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _codex_store()

    def fake_login() -> None:
        # Simulate `codex login --device-auth` writing through the active symlink.
        store.auth_path.write_text(json.dumps(_codex_auth("acct-new")), encoding="utf-8")

    monkeypatch.setattr(store, "_run_headless_login", fake_login)

    store.add_account(label="work", slot=1)

    assert store.auth_path.is_symlink()
    assert store.auth_path.resolve() == store._auth_backup_path("1").resolve()
    assert '"acct-new"' in store._auth_backup_path("1").read_text(encoding="utf-8")
    data = store._sequence_data()
    assert data["accounts"]["1"]["label"] == "work"
    assert data["activeAccountNumber"] == 1


def test_codex_add_preserves_prior_managed_login(temp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    # account 1 is the current live real auth.json (freshest copy)
    store._set_account_record(data, "1", "one", store._metadata(json.dumps(_codex_auth("acct-1"))))
    store._write_json(store.sequence_file, data)
    fresh_one = {"auth_mode": "chatgpt", "tokens": {"account_id": "acct-1", "access_token": "FRESH1"}}
    _write(store.auth_path, fresh_one)

    def fake_login() -> None:
        store.auth_path.write_text(json.dumps(_codex_auth("acct-2")), encoding="utf-8")

    monkeypatch.setattr(store, "_run_headless_login", fake_login)

    store.add_account(label="two", slot=2)

    # account 1's fresh live bytes were preserved into its target before repointing
    assert '"FRESH1"' in store._auth_backup_path("1").read_text(encoding="utf-8")
    assert store.auth_path.resolve() == store._auth_backup_path("2").resolve()


def test_codex_add_restores_symlink_when_login_fails(temp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    _write(store._auth_backup_path("1"), _codex_auth("acct-1"))
    store._set_account_record(data, "1", "one", store._metadata(json.dumps(_codex_auth("acct-1"))))
    store._write_json(store.sequence_file, data)
    store._activate_auth_symlink(store._auth_backup_path("1"))

    def boom() -> None:
        raise ConfigError("codex CLI not found; run 'codex login --device-auth' manually")

    monkeypatch.setattr(store, "_run_headless_login", boom)

    with pytest.raises(ConfigError, match="codex CLI not found"):
        store.add_account(label=None, slot=2)

    # prior account-1 symlink restored; not left dangling on account-2
    assert store.auth_path.is_symlink()
    assert store.auth_path.resolve() == store._auth_backup_path("1").resolve()
