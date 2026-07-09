from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from claude_swap.exceptions import ConfigError
from claude_swap.paths import get_backup_root, get_provider_store_root
from claude_swap.providers.frontends import CodexFrontend, OpencodeFrontend
from claude_swap.providers.openai import CodexOpenAIBackend, OpencodeOpenAIBackend
from claude_swap.providers.store import ProviderAccountStore
from claude_swap.providers.types import ProviderDefinition, ProviderRef, RefreshResult


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
    monkeypatch.setattr(store, "_run_headless_login", lambda: _fake_codex_login(store, auth_payload))

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


def _fake_codex_login(store: ProviderAccountStore, payload: dict[str, object]) -> None:
    # Real `codex login` deletes auth.json (removing a symlink) then writes a
    # fresh REAL file - it does NOT write through a symlink.
    store.auth_path.unlink(missing_ok=True)
    store.auth_path.parent.mkdir(parents=True, exist_ok=True)
    store.auth_path.write_text(json.dumps(payload), encoding="utf-8")


def test_codex_add_runs_device_login_then_registers(temp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _codex_store()

    monkeypatch.setattr(store, "_run_headless_login", lambda: _fake_codex_login(store, _codex_auth("acct-new")))

    store.add_account(label="work", slot=1)

    assert store.auth_path.is_symlink()
    assert store.auth_path.resolve() == store._auth_backup_path("1").resolve()
    assert '"acct-new"' in store._auth_backup_path("1").read_text(encoding="utf-8")
    data = store._sequence_data()
    assert data["accounts"]["1"]["label"] == "work"
    assert data["activeAccountNumber"] == 1


def test_codex_add_relogin_persists_fresh_creds_into_existing_slot(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Re-login by label of an already-registered account must UPDATE that slot in
    # place with the fresh credential, not mint a new slot (the reported bug).
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    _write(store._auth_backup_path("2"), _codex_auth("BBB"))  # stale stored target
    store._set_account_record(data, "2", "jparr721@pm.me", store._metadata(json.dumps(_codex_auth("BBB"))))
    store._write_json(store.sequence_file, data)
    store._activate_auth_symlink(store._auth_backup_path("2"))
    fresh = {"auth_mode": "chatgpt", "tokens": {"account_id": "BBB", "access_token": "FRESH"}}

    monkeypatch.setattr(store, "_run_headless_login", lambda: _fake_codex_login(store, fresh))

    store.add_account(label="jparr721@pm.me", slot=None)

    data = store._sequence_data()
    assert set(data["accounts"].keys()) == {"2"}  # no new slot minted
    assert '"FRESH"' in store._auth_backup_path("2").read_text(encoding="utf-8")  # persisted into slot 2
    assert store.auth_path.is_symlink()
    assert store.auth_path.resolve() == store._auth_backup_path("2").resolve()


def test_codex_add_relogin_by_identity_updates_existing_slot(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No --slot, no matching label: identity (account_id) match still updates the
    # existing account rather than creating a duplicate.
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    _write(store._auth_backup_path("2"), _codex_auth("BBB"))
    store._set_account_record(data, "2", "personal", store._metadata(json.dumps(_codex_auth("BBB"))))
    store._write_json(store.sequence_file, data)
    store._activate_auth_symlink(store._auth_backup_path("2"))
    fresh = {"auth_mode": "chatgpt", "tokens": {"account_id": "BBB", "access_token": "FRESH2"}}

    monkeypatch.setattr(store, "_run_headless_login", lambda: _fake_codex_login(store, fresh))

    store.add_account(label=None, slot=None)

    data = store._sequence_data()
    assert set(data["accounts"].keys()) == {"2"}
    assert '"FRESH2"' in store._auth_backup_path("2").read_text(encoding="utf-8")


def test_codex_add_clears_active_auth_before_login_to_avoid_revoke(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `codex login` revokes whatever is in auth.json before logging in the new
    # account. Adding an account must clear auth.json first so the outgoing
    # account is never revoked (its creds are preserved in its own file).
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    _write(store._auth_backup_path("1"), _codex_auth("AAA"))
    store._set_account_record(data, "1", "work", store._metadata(json.dumps(_codex_auth("AAA"))))
    store._write_json(store.sequence_file, data)
    store._activate_auth_symlink(store._auth_backup_path("1"))  # account-1 is active

    seen: dict[str, bool] = {}

    def fake_login() -> None:
        seen["auth_live_at_login"] = store.auth_path.exists() or store.auth_path.is_symlink()
        _fake_codex_login(store, _codex_auth("BBB"))

    monkeypatch.setattr(store, "_run_headless_login", fake_login)

    store.add_account(label="personal", slot=2)

    assert seen["auth_live_at_login"] is False  # cleared before login => codex revokes nothing
    assert '"AAA"' in store._auth_backup_path("1").read_text(encoding="utf-8")  # outgoing preserved


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

    monkeypatch.setattr(store, "_run_headless_login", lambda: _fake_codex_login(store, _codex_auth("acct-2")))

    store.add_account(label="two", slot=2)

    # account 1's fresh live bytes were preserved into its target before login clobbered auth.json
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


def test_first_switch_adopts_live_real_file_and_symlinks(temp_home: Path) -> None:
    # Pre-change world: accounts 1 & 2 registered with byte-snapshot targets, and
    # ~/.codex/auth.json is a REAL file = freshest account-1 creds.
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    for num, acct in (("1", "acct-1"), ("2", "acct-2")):
        _write(store._auth_backup_path(num), _codex_auth(acct))
        store._set_account_record(data, num, f"a{num}", store._metadata(json.dumps(_codex_auth(acct))))
    store._write_json(store.sequence_file, data)
    fresh_one = {"auth_mode": "chatgpt", "tokens": {"account_id": "acct-1", "access_token": "FRESH"}}
    _write(store.auth_path, fresh_one)  # real file, not yet a symlink

    store.switch("2", json_output=False)

    # account-1 preserved from the live file, active now symlinked to account-2
    assert '"FRESH"' in store._auth_backup_path("1").read_text(encoding="utf-8")
    assert store.auth_path.is_symlink()
    assert store.auth_path.resolve() == store._auth_backup_path("2").resolve()


def _jwt_with_exp(exp: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def _codex_oauth_auth(account_id: str, exp: int) -> dict[str, object]:
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "account_id": account_id,
            "access_token": _jwt_with_exp(exp),
            "refresh_token": f"refresh-{account_id}",
        },
    }


_EXPIRED = 1_000_000_000  # 2001
_FRESH = 4_000_000_000  # 2096


def _seeded_codex_store(
    accounts: dict[str, dict[str, object]], active: str
) -> ProviderAccountStore:
    """Store with the given account files, records, and active symlink."""
    store = _codex_store()
    store._setup_directories()
    store._init_sequence_file()
    data = store._sequence_data()
    for num, payload in accounts.items():
        _write(store._auth_backup_path(num), payload)
        store._set_account_record(
            data, num, f"acct-label-{num}", store._metadata(json.dumps(payload))
        )
    data["activeAccountNumber"] = int(active)
    store._write_json(store.sequence_file, data)
    store._activate_auth_symlink(store._auth_backup_path(active))
    return store


def test_inactive_expired_account_is_refreshed_and_persisted(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _FRESH), "2": _codex_oauth_auth("acct-2", _EXPIRED)},
        active="1",
    )
    rotated = dict(_codex_oauth_auth("acct-2", _FRESH))
    rotated["tokens"]["refresh_token"] = "rotated-refresh"  # type: ignore[index]
    rotated_text = json.dumps(rotated)
    refresh_calls: list[str] = []
    fetch_auths: list[str] = []

    def fake_refresh(auth_text: str, timeout_s: float) -> RefreshResult:
        refresh_calls.append(auth_text)
        return RefreshResult(rotated_text, None)

    def fake_fetch(auth_text: str, timeout_s: float) -> dict[str, object]:
        fetch_auths.append(auth_text)
        return {"windows": [{"label": "5h", "pct": 10.0}]}

    monkeypatch.setattr(store.definition.backend, "refresh_auth", fake_refresh)
    monkeypatch.setattr(store.definition.backend, "fetch_usage", fake_fetch)

    payload = store.list_accounts(json_output=True)

    assert len(refresh_calls) == 1  # only the inactive expired account
    assert json.loads(refresh_calls[0])["tokens"]["account_id"] == "acct-2"
    assert rotated_text in fetch_auths  # usage fetched with the rotated token
    on_disk = json.loads(store._auth_backup_path("2").read_text(encoding="utf-8"))
    assert on_disk["tokens"]["refresh_token"] == "rotated-refresh"
    rows = {row["number"]: row for row in payload["accounts"]}
    assert rows[2]["usageStatus"] == "ok"


def test_active_account_is_never_refreshed_even_when_expired(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _EXPIRED), "2": _codex_oauth_auth("acct-2", _FRESH)},
        active="1",
    )
    refresh_calls: list[str] = []

    def fake_refresh(auth_text: str, timeout_s: float) -> RefreshResult:
        refresh_calls.append(auth_text)
        return RefreshResult(auth_text, None)

    monkeypatch.setattr(store.definition.backend, "refresh_auth", fake_refresh)
    monkeypatch.setattr(
        store.definition.backend, "fetch_usage", lambda auth_text, timeout_s: {"windows": []}
    )

    store.list_accounts(json_output=True)

    assert refresh_calls == []


def test_refresh_discarded_when_account_becomes_active_mid_refresh(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _FRESH), "2": _codex_oauth_auth("acct-2", _EXPIRED)},
        active="1",
    )
    original_text = store._auth_backup_path("2").read_text(encoding="utf-8")

    def fake_refresh(auth_text: str, timeout_s: float) -> RefreshResult:
        # A concurrent switch repoints the symlink at the account under refresh.
        store._activate_auth_symlink(store._auth_backup_path("2"))
        return RefreshResult(json.dumps(_codex_oauth_auth("acct-2", _FRESH)), None)

    monkeypatch.setattr(store.definition.backend, "refresh_auth", fake_refresh)
    monkeypatch.setattr(
        store.definition.backend, "fetch_usage", lambda auth_text, timeout_s: {"windows": []}
    )

    payload = store.list_accounts(json_output=True)

    assert store._auth_backup_path("2").read_text(encoding="utf-8") == original_text
    rows = {row["number"]: row for row in payload["accounts"]}
    assert rows[2]["usageStatus"] == "unavailable"
    assert rows[2]["usageError"] == "token refresh not persisted"


def test_refresh_discarded_when_disk_auth_changed_mid_refresh(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _FRESH), "2": _codex_oauth_auth("acct-2", _EXPIRED)},
        active="1",
    )
    concurrent = json.dumps(_codex_oauth_auth("acct-2-relogin", _FRESH))

    def fake_refresh(auth_text: str, timeout_s: float) -> RefreshResult:
        # A concurrent re-login rewrites the account file while we refresh.
        store._auth_backup_path("2").write_text(concurrent, encoding="utf-8")
        return RefreshResult(json.dumps(_codex_oauth_auth("acct-2", _FRESH)), None)

    monkeypatch.setattr(store.definition.backend, "refresh_auth", fake_refresh)
    monkeypatch.setattr(
        store.definition.backend, "fetch_usage", lambda auth_text, timeout_s: {"windows": []}
    )

    store.list_accounts(json_output=True)

    assert store._auth_backup_path("2").read_text(encoding="utf-8") == concurrent


def test_persist_write_failure_is_transient_error_not_success(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _FRESH), "2": _codex_oauth_auth("acct-2", _EXPIRED)},
        active="1",
    )
    monkeypatch.setattr(
        store.definition.backend,
        "refresh_auth",
        lambda auth_text, timeout_s: RefreshResult(
            json.dumps(_codex_oauth_auth("acct-2", _FRESH)), None
        ),
    )
    monkeypatch.setattr(
        store.definition.backend, "fetch_usage", lambda auth_text, timeout_s: {"windows": []}
    )

    def broken_write(account_num: str, text: str) -> None:
        raise ConfigError("disk full")

    monkeypatch.setattr(store, "_write_account_auth", broken_write)

    payload = store.list_accounts(json_output=True)

    rows = {row["number"]: row for row in payload["accounts"]}
    assert rows[2]["usageStatus"] == "unavailable"
    assert rows[2]["usageError"] == "token refresh not persisted"


def test_transient_refresh_failure_backs_off_without_persist(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _FRESH), "2": _codex_oauth_auth("acct-2", _EXPIRED)},
        active="1",
    )
    original_text = store._auth_backup_path("2").read_text(encoding="utf-8")
    monkeypatch.setattr(
        store.definition.backend,
        "refresh_auth",
        lambda auth_text, timeout_s: RefreshResult(None, "transient"),
    )
    monkeypatch.setattr(
        store.definition.backend, "fetch_usage", lambda auth_text, timeout_s: {"windows": []}
    )

    payload = store.list_accounts(json_output=True)

    assert store._auth_backup_path("2").read_text(encoding="utf-8") == original_text
    rows = {row["number"]: row for row in payload["accounts"]}
    assert rows[2]["usageError"] == "token refresh failed"


def test_no_refresh_token_falls_through_to_plain_fetch(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_no_refresh = {
        "auth_mode": "chatgpt",
        "tokens": {"account_id": "acct-2", "access_token": _jwt_with_exp(_EXPIRED)},
    }
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _FRESH), "2": auth_no_refresh}, active="1"
    )
    fetch_auths: list[str] = []

    def fake_fetch(auth_text: str, timeout_s: float) -> dict[str, object]:
        fetch_auths.append(auth_text)
        return {"windows": []}

    monkeypatch.setattr(store.definition.backend, "fetch_usage", fake_fetch)

    store.list_accounts(json_output=True)

    # The real refresh_auth returned no_refresh_token; usage was still fetched
    # with the on-disk auth, unchanged.
    assert json.dumps(auth_no_refresh) in fetch_auths


def test_opencode_accounts_never_hit_the_refresh_path(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = temp_home / ".local" / "share" / "opencode" / "auth.json"
    _write(auth_path, _opencode_auth("acct-1"))
    store = _opencode_store()
    store.add_account(label="one", slot=1)
    fetch_calls: list[str] = []

    def fake_fetch(auth_text: str, timeout_s: float) -> dict[str, object]:
        fetch_calls.append(auth_text)
        return {"windows": []}

    monkeypatch.setattr(store.definition.backend, "fetch_usage", fake_fetch)

    store.list_accounts(json_output=True)

    assert len(fetch_calls) == 1  # fetched as today, no refresh interference


def test_invalid_grant_quarantines_account_and_reports_relogin(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _FRESH), "2": _codex_oauth_auth("acct-2", _EXPIRED)},
        active="1",
    )
    refresh_calls: list[str] = []

    def fake_refresh(auth_text: str, timeout_s: float) -> RefreshResult:
        refresh_calls.append(auth_text)
        return RefreshResult(None, "invalid_grant")

    fetched: list[str] = []

    def fake_fetch(auth_text: str, timeout_s: float) -> dict[str, object]:
        fetched.append(json.loads(auth_text)["tokens"]["account_id"])
        return {"windows": []}

    monkeypatch.setattr(store.definition.backend, "refresh_auth", fake_refresh)
    monkeypatch.setattr(store.definition.backend, "fetch_usage", fake_fetch)

    payload = store.list_accounts(json_output=True)
    rows = {row["number"]: row for row in payload["accounts"]}
    assert rows[2]["usageStatus"] == "relogin_required"
    assert "acct-2" not in fetched  # the dead account's usage was never fetched

    # A second pass must not retry the dead refresh token (quarantine).
    payload = store.list_accounts(json_output=True)
    assert len(refresh_calls) == 1
    rows = {row["number"]: row for row in payload["accounts"]}
    assert rows[2]["usageStatus"] == "relogin_required"


def test_relogin_needed_appears_in_human_output(
    temp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _seeded_codex_store(
        {"1": _codex_oauth_auth("acct-1", _FRESH), "2": _codex_oauth_auth("acct-2", _EXPIRED)},
        active="1",
    )
    monkeypatch.setattr(
        store.definition.backend,
        "refresh_auth",
        lambda auth_text, timeout_s: RefreshResult(None, "invalid_grant"),
    )
    monkeypatch.setattr(
        store.definition.backend, "fetch_usage", lambda auth_text, timeout_s: {"windows": []}
    )

    store.list_accounts(json_output=False)

    out = capsys.readouterr().out
    assert "re-login needed" in out
    assert "cswap codex openai add" in out
