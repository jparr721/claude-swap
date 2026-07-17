from __future__ import annotations

import base64
import io
import json
import urllib.error
from pathlib import Path

import pytest

from claude_swap.providers import openai as openai_module
from claude_swap.providers.frontends import CodexFrontend
from claude_swap.providers.openai import (
    ACCESS_TOKEN_EXPIRY_BUFFER_S,
    CODEX_OAUTH_CLIENT_ID,
    CODEX_REFRESH_TOKEN_URL,
    CodexOpenAIBackend,
)
from claude_swap.providers.types import RefreshResult


def _codex_auth(account_id: str) -> dict[str, object]:
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "account_id": account_id,
            "access_token": f"access-{account_id}",
            "refresh_token": f"refresh-{account_id}",
        },
    }


def test_codex_frontend_uses_codex_auth_path(temp_home: Path) -> None:
    frontend = CodexFrontend()

    assert frontend.active_auth_path() == temp_home / ".codex" / "auth.json"


def test_codex_openai_metadata_reads_tokens() -> None:
    metadata = CodexOpenAIBackend().metadata_from_text(json.dumps(_codex_auth("acct-1")))

    assert metadata.account_id == "acct-1"
    assert metadata.auth_mode == "chatgpt"
    assert metadata.access_token == "access-acct-1"
    assert metadata.fingerprint


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


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=CODEX_REFRESH_TOKEN_URL,
        code=code,
        msg="err",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )


def _full_codex_auth_text() -> str:
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "agent_identity": {"kind": "personal"},
            "custom_future_field": "keep-me",
            "tokens": {
                "account_id": "acct-1",
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "id_token": "old-id",
            },
            "last_refresh": "2026-01-01T00:00:00Z",
        }
    )


def test_refresh_auth_success_rotates_and_preserves_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CodexOpenAIBackend()
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse({"access_token": "new-access", "refresh_token": "new-refresh"})

    monkeypatch.setattr(openai_module.urllib.request, "urlopen", fake_urlopen)
    result = backend.refresh_auth(_full_codex_auth_text(), 10.0)

    assert result.error is None
    assert captured["url"] == CODEX_REFRESH_TOKEN_URL
    assert captured["body"] == {
        "client_id": CODEX_OAUTH_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
        "scope": "openid profile email",
    }
    rotated = json.loads(result.auth_text)
    assert rotated["tokens"]["access_token"] == "new-access"
    assert rotated["tokens"]["refresh_token"] == "new-refresh"
    assert rotated["tokens"]["id_token"] == "old-id"  # absent in response: retained
    assert rotated["tokens"]["account_id"] == "acct-1"
    assert rotated["auth_mode"] == "chatgpt"
    assert rotated["agent_identity"] == {"kind": "personal"}
    assert rotated["custom_future_field"] == "keep-me"
    assert rotated["last_refresh"] != "2026-01-01T00:00:00Z"


def test_refresh_auth_400_invalid_grant_is_permanent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CodexOpenAIBackend()

    def fake_urlopen(request, timeout):
        raise _http_error(400, '{"error": "invalid_grant"}')

    monkeypatch.setattr(openai_module.urllib.request, "urlopen", fake_urlopen)
    result = backend.refresh_auth(_full_codex_auth_text(), 10.0)
    assert result == RefreshResult(None, "invalid_grant")


def test_refresh_auth_reused_token_marker_is_permanent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CodexOpenAIBackend()

    def fake_urlopen(request, timeout):
        raise _http_error(401, '{"error": "refresh_token_reused"}')

    monkeypatch.setattr(openai_module.urllib.request, "urlopen", fake_urlopen)
    assert backend.refresh_auth(_full_codex_auth_text(), 10.0).error == "invalid_grant"


def test_refresh_auth_5xx_with_invalid_grant_body_stays_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CodexOpenAIBackend()

    def fake_urlopen(request, timeout):
        raise _http_error(500, '{"error": "invalid_grant"}')

    monkeypatch.setattr(openai_module.urllib.request, "urlopen", fake_urlopen)
    assert backend.refresh_auth(_full_codex_auth_text(), 10.0).error == "transient"


def test_refresh_auth_network_error_is_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CodexOpenAIBackend()

    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(openai_module.urllib.request, "urlopen", fake_urlopen)
    assert backend.refresh_auth(_full_codex_auth_text(), 10.0).error == "transient"


def test_refresh_auth_without_refresh_token() -> None:
    backend = CodexOpenAIBackend()
    text = json.dumps({"tokens": {"account_id": "a", "access_token": "x"}})
    assert backend.refresh_auth(text, 10.0) == RefreshResult(
        None, "no_refresh_token"
    )


def test_refresh_auth_api_key_only_auth() -> None:
    backend = CodexOpenAIBackend()
    text = json.dumps({"openai_api_key": "sk-x"})
    assert backend.refresh_auth(text, 10.0).error == "no_refresh_token"
