from __future__ import annotations

import base64
import email.message
import json
import urllib.error
from pathlib import Path

import pytest

from claude_swap.providers.frontends import CodexFrontend, OpencodeFrontend
from claude_swap.providers.openai import (
    ACCESS_TOKEN_EXPIRY_BUFFER_S,
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


def _jwt_with_exp(exp: object) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def _codex_auth_text(access_token: str, refresh_token: str = "refresh-1") -> str:
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "account_id": "acct-1",
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        }
    )


def test_expired_jwt_access_token_is_expired() -> None:
    backend = CodexOpenAIBackend()
    assert backend.access_token_expired(_codex_auth_text(_jwt_with_exp(1_000_000_000))) is True


def test_far_future_jwt_access_token_is_not_expired() -> None:
    backend = CodexOpenAIBackend()
    assert backend.access_token_expired(_codex_auth_text(_jwt_with_exp(4_000_000_000))) is False


def test_jwt_within_expiry_buffer_is_expired() -> None:
    import time

    backend = CodexOpenAIBackend()
    almost = int(time.time()) + ACCESS_TOKEN_EXPIRY_BUFFER_S - 30
    assert backend.access_token_expired(_codex_auth_text(_jwt_with_exp(almost))) is True


def test_unparseable_access_token_is_expired() -> None:
    backend = CodexOpenAIBackend()
    assert backend.access_token_expired(_codex_auth_text("not-a-jwt")) is True


def test_jwt_without_exp_claim_is_expired() -> None:
    backend = CodexOpenAIBackend()
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(b'{"sub":"x"}').rstrip(b"=").decode()
    assert backend.access_token_expired(_codex_auth_text(f"{header}.{payload}.sig")) is True


def test_missing_access_token_with_tokens_dict_is_expired() -> None:
    backend = CodexOpenAIBackend()
    text = json.dumps({"tokens": {"account_id": "acct-1", "refresh_token": "r"}})
    assert backend.access_token_expired(text) is True


def test_api_key_only_auth_is_never_expired() -> None:
    backend = CodexOpenAIBackend()
    assert backend.access_token_expired(json.dumps({"openai_api_key": "sk-x"})) is False


def test_invalid_json_auth_is_never_expired() -> None:
    backend = CodexOpenAIBackend()
    assert backend.access_token_expired("{not json") is False


def test_opencode_access_token_never_expired() -> None:
    backend = OpencodeOpenAIBackend()
    text = json.dumps({"openai": {"access": _jwt_with_exp(1_000_000_000), "refresh": "r"}})
    assert backend.access_token_expired(text) is False
