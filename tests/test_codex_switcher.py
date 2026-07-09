"""Tests for provider-backed Codex compatibility wrappers."""

from __future__ import annotations

import email.message
import json
import urllib.error
from pathlib import Path

import pytest

from claude_swap.codex import (
    CODEX_USAGE_TIMEOUT_S,
    CODEX_USAGE_URL,
    CodexAccountSwitcher,
    OpencodeAccountSwitcher,
    _UsageFetchError,
    fetch_codex_usage,
    fetch_opencode_usage,
)
from claude_swap.exceptions import ConfigError


def _http_error(code: int, retry_after: str | None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://chatgpt.com/x", code, "err", headers, None)


def _write_codex_auth(home: Path, payload: dict[str, object]) -> Path:
    auth_path = home / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(json.dumps(payload), encoding="utf-8")
    return auth_path


def _write_opencode_auth(home: Path, payload: dict[str, object]) -> Path:
    auth_path = home / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(json.dumps(payload), encoding="utf-8")
    return auth_path


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


def test_fetch_codex_usage_calls_wham_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "rate_limit": {
                        "primary_window": {
                            "limit_window_seconds": 10800,
                            "used_percent": 25,
                            "reset_at": 1783458000,
                        },
                        "secondary_window": {
                            "limit_window_seconds": 604800,
                            "used_percent": 50,
                            "reset_at": 1784062800,
                        },
                    },
                    "plan_type": "plus",
                    "credits": {"balance": "2"},
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["account"] = request.get_header("Chatgpt-account-id")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("claude_swap.providers.openai.urllib.request.urlopen", fake_urlopen)

    usage = fetch_codex_usage(json.dumps(_codex_auth("acct-1")), timeout_s=3.0)

    assert captured == {
        "url": CODEX_USAGE_URL,
        "authorization": "Bearer token-acct-1",
        "account": "acct-1",
        "timeout": 3.0,
    }
    assert usage == {
        "windows": [
            {"label": "3h", "pct": 25.0, "resets_at": "2026-07-07T21:00:00Z"},
            {"label": "7d", "pct": 50.0, "resets_at": "2026-07-14T21:00:00Z"},
        ],
        "plan": "plus",
        "credits": 2.0,
    }


def test_fetch_codex_usage_429_carries_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout):
        raise _http_error(429, "300")

    monkeypatch.setattr("claude_swap.providers.openai.urllib.request.urlopen", fake_urlopen)

    result = fetch_codex_usage(json.dumps(_codex_auth("acct-1")), timeout_s=3.0)

    assert result == _UsageFetchError("HTTP 429", 300.0)


def test_fetch_opencode_usage_uses_openai_oauth_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps({"rate_limit": {}}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["account"] = request.get_header("Chatgpt-account-id")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("claude_swap.providers.openai.urllib.request.urlopen", fake_urlopen)

    result = fetch_opencode_usage(json.dumps(_opencode_auth("acct-1")), timeout_s=3.0)

    assert captured == {
        "authorization": "Bearer token-acct-1",
        "account": "acct-1",
        "timeout": 3.0,
    }
    assert result == {"windows": []}


def test_codex_wrapper_uses_provider_store_and_compat_usage_hook(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    switcher = CodexAccountSwitcher()
    calls: list[tuple[str, float]] = []

    def fake_fetch(auth_text: str, timeout_s: float) -> dict[str, object]:
        calls.append((auth_text, timeout_s))
        return {"windows": [{"label": "3h", "pct": 25.0}]}

    monkeypatch.setattr("claude_swap.codex.fetch_codex_usage", fake_fetch)

    def fake_login() -> None:
        switcher._store.auth_path.write_text(json.dumps(_codex_auth("acct-1")), encoding="utf-8")

    monkeypatch.setattr(switcher._store, "_run_headless_login", fake_login)

    switcher.add_account(label="work", slot=1)
    payload = switcher.list_accounts(json_output=True)

    assert calls == [(json.dumps(_codex_auth("acct-1")), CODEX_USAGE_TIMEOUT_S)]
    assert payload["schemaVersion"] == 1
    assert payload["provider"] == {"frontend": "codex", "backend": "openai"}
    assert payload["activeAccountNumber"] == 1
    assert payload["accounts"][0]["label"] == "work"
    assert payload["accounts"][0]["usageStatus"] == "ok"


def test_opencode_wrapper_refuses_to_restore_openai_oauth_snapshot(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = _write_opencode_auth(temp_home, _opencode_auth("acct-1"))
    switcher = OpencodeAccountSwitcher()
    monkeypatch.setattr(
        "claude_swap.codex.fetch_opencode_usage",
        lambda auth_text, timeout_s: {"windows": []},
    )
    switcher.add_account(label="one", slot=1)
    auth_path.write_text(json.dumps(_opencode_auth("acct-2")), encoding="utf-8")
    switcher.add_account(label="two", slot=2)

    with pytest.raises(ConfigError, match="cannot safely restore stored OpenAI OAuth"):
        switcher.switch("1", json_output=False)

    active = json.loads(auth_path.read_text(encoding="utf-8"))
    assert active["openai"]["accountId"] == "acct-2"
    assert switcher.status(json_output=True)["active"] == {
        "number": 2,
        "label": "two",
        "managed": True,
    }
    assert switcher.list_accounts(json_output=True)["provider"] == {
        "frontend": "opencode",
        "backend": "openai",
    }


def test_codex_wrapper_add_surfaces_login_failure(
    temp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    switcher = CodexAccountSwitcher()

    def boom() -> None:
        raise ConfigError("codex CLI not found; run 'codex login --device-auth' manually")

    monkeypatch.setattr(switcher._store, "_run_headless_login", boom)

    with pytest.raises(ConfigError, match="codex CLI not found"):
        switcher.add_account(label=None, slot=None)
