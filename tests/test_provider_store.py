from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_swap.exceptions import ConfigError
from claude_swap.paths import get_provider_store_root
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
        )
    )


def test_codex_store_adds_and_lists_account(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_payload = _codex_auth("acct-1")
    _write(temp_home / ".codex" / "auth.json", auth_payload)
    store = _codex_store()
    backend_calls: list[tuple[str, float]] = []

    def _fake_fetch_usage(auth_text: str, timeout_s: float) -> dict[str, object]:
        backend_calls.append((auth_text, timeout_s))
        return {"windows": [{"label": "Monthly", "pct": 12.0}]}

    monkeypatch.setattr(store.definition.backend, "fetch_usage", _fake_fetch_usage)

    store.add_account(label="work", slot=1)
    payload = store.list_accounts(json_output=True)

    assert backend_calls == [(json.dumps(auth_payload), 10.0)]
    assert payload["schemaVersion"] == 2
    assert payload["provider"] == {"frontend": "codex", "backend": "openai"}
    assert payload["activeAccountNumber"] == 1
    assert payload["accounts"][0]["label"] == "work"
    assert payload["accounts"][0]["active"] is True


def test_opencode_store_switches_only_opencode_auth(temp_home: Path) -> None:
    auth_path = temp_home / ".local" / "share" / "opencode" / "auth.json"
    _write(auth_path, _opencode_auth("acct-1"))
    store = _opencode_store()
    store.add_account(label="one", slot=1)
    _write(auth_path, _opencode_auth("acct-2"))
    store.add_account(label="two", slot=2)

    store.switch("1", json_output=False)

    assert json.loads(auth_path.read_text(encoding="utf-8"))["openai"]["accountId"] == "acct-1"
    assert store.status(json_output=True)["active"] == {
        "number": 1,
        "label": "one",
        "managed": True,
    }


def test_missing_active_auth_mentions_provider_login(temp_home: Path) -> None:
    store = _opencode_store()

    with pytest.raises(ConfigError, match="opencode auth"):
        store.add_account(label=None, slot=None)
