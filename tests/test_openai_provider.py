from __future__ import annotations

import email.message
import json
import urllib.error
from pathlib import Path

import pytest

from claude_swap.providers.frontends import CodexFrontend, OpencodeFrontend
from claude_swap.providers.openai import (
    CodexOpenAIBackend,
    OpencodeOpenAIBackend,
    UsageFetchError,
)


def _codex_auth(account_id: str) -> dict[str, object]:
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "account_id": account_id,
            "access_token": f"access-{account_id}",
            "refresh_token": f"refresh-{account_id}",
        },
    }


def _opencode_auth(account_id: str) -> dict[str, object]:
    return {
        "openai": {
            "type": "oauth",
            "access": f"access-{account_id}",
            "refresh": f"refresh-{account_id}",
            "expires": 1784223299464,
            "accountId": account_id,
        }
    }


def test_codex_frontend_uses_codex_auth_path(temp_home: Path) -> None:
    frontend = CodexFrontend()

    assert frontend.active_auth_path() == temp_home / ".codex" / "auth.json"


def test_opencode_frontend_uses_opencode_auth_path(temp_home: Path) -> None:
    frontend = OpencodeFrontend()

    assert (
        frontend.active_auth_path()
        == temp_home / ".local" / "share" / "opencode" / "auth.json"
    )


def test_codex_openai_metadata_reads_tokens() -> None:
    metadata = CodexOpenAIBackend().metadata_from_text(json.dumps(_codex_auth("acct-1")))

    assert metadata.account_id == "acct-1"
    assert metadata.auth_mode == "chatgpt"
    assert metadata.access_token == "access-acct-1"
    assert metadata.fingerprint


def test_opencode_openai_metadata_reads_openai_entry() -> None:
    metadata = OpencodeOpenAIBackend().metadata_from_text(
        json.dumps(_opencode_auth("acct-1"))
    )

    assert metadata.account_id == "acct-1"
    assert metadata.auth_mode == "oauth"
    assert metadata.access_token == "access-acct-1"
    assert metadata.fingerprint


def test_opencode_openai_usage_401_is_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = email.message.Message()
    exc = urllib.error.HTTPError("https://chatgpt.com/x", 401, "err", headers, None)

    def fake_urlopen(request, timeout):
        raise exc

    monkeypatch.setattr("claude_swap.providers.openai.urllib.request.urlopen", fake_urlopen)

    result = OpencodeOpenAIBackend().fetch_usage(
        json.dumps(_opencode_auth("acct-1")), timeout_s=3.0
    )

    assert result == UsageFetchError("token expired", None)
