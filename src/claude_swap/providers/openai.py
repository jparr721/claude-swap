"""OpenAI provider auth parsing and usage fetching."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from claude_swap.exceptions import ConfigError
from claude_swap.providers.types import AuthMetadata, RefreshResult, UsageFetchError

OPENAI_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
OPENAI_USAGE_TIMEOUT_S = 10.0

# Refresh decisions use the same 5-minute pre-expiry buffer as oauth.py.
ACCESS_TOKEN_EXPIRY_BUFFER_S = 5 * 60

# Source-verified from openai/codex codex-rs/login/src/auth/manager.rs @ rust-v0.143.0.
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_REFRESH_TOKEN_URL = "https://auth.openai.com/oauth/token"

_logger = logging.getLogger("claude-swap")


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _jwt_exp_seconds(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return float(exp)


def _safe_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _unix_seconds_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _clamp_percent(value: Any) -> float:
    if isinstance(value, (int, float)):
        pct = float(value)
    else:
        pct = 0.0
    return min(100.0, max(0.0, pct))


def _window_label(window: dict[str, Any]) -> str:
    seconds = window.get("limit_window_seconds")
    if not isinstance(seconds, (int, float)):
        return "?"
    hours = round(seconds / 3600)
    if hours >= 24:
        return f"{round(hours / 24)}d"
    return f"{hours}h"


def parse_openai_usage(data: dict[str, Any]) -> dict[str, Any]:
    rate_limit = data.get("rate_limit")
    if not isinstance(rate_limit, dict):
        rate_limit = {}
    windows: list[dict[str, Any]] = []
    for key in ("primary_window", "secondary_window"):
        window = rate_limit.get(key)
        if not isinstance(window, dict):
            continue
        entry: dict[str, Any] = {
            "label": _window_label(window),
            "pct": _clamp_percent(window.get("used_percent")),
        }
        resets_at = _unix_seconds_to_iso(window.get("reset_at"))
        if resets_at is not None:
            entry["resets_at"] = resets_at
        windows.append(entry)

    usage: dict[str, Any] = {"windows": windows}
    plan = _safe_str(data.get("plan_type"))
    if plan:
        usage["plan"] = plan
    credits = data.get("credits")
    if isinstance(credits, dict):
        balance = credits.get("balance")
    else:
        balance = None
    if isinstance(balance, (int, float)):
        usage["credits"] = float(balance)
    elif isinstance(balance, str):
        try:
            usage["credits"] = float(balance)
        except ValueError:
            pass
    return usage


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    raw = exc.headers.get("Retry-After") if exc.headers else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None


def fetch_openai_usage(
    auth_text: str,
    timeout_s: float,
    metadata_parser: Callable[[str], AuthMetadata],
    user_agent: str,
) -> dict[str, Any] | str | UsageFetchError:
    metadata = metadata_parser(auth_text)
    if not metadata.access_token:
        return "no access token"
    headers = {
        "Authorization": f"Bearer {metadata.access_token}",
        "Accept": "application/json",
        "originator": "claude-swap",
        "User-Agent": user_agent,
    }
    if metadata.account_id:
        headers["ChatGPT-Account-Id"] = metadata.account_id
    request = urllib.request.Request(OPENAI_USAGE_URL, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        retry_after = _retry_after_seconds(exc)
        if exc.code in (401, 403):
            return UsageFetchError("token expired", retry_after, exc.code)
        return UsageFetchError(f"HTTP {exc.code}", retry_after, exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        message = str(exc)
        if not message:
            message = "usage unavailable"
        return message

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return "malformed usage response"
    if not isinstance(data, dict):
        return "malformed usage response"
    return parse_openai_usage(data)


class CodexOpenAIBackend:
    backend_id = "openai"
    display_name = "OpenAI"

    def metadata_from_text(self, text: str) -> AuthMetadata:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Codex auth file is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("Codex auth file must contain a JSON object")
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            token_data = tokens
        else:
            token_data = {}
        token_fields = ("account_id", "access_token", "refresh_token", "id_token")
        has_token = any(_safe_str(token_data.get(field)) for field in token_fields)
        has_key = any(
            _safe_str(data.get(field))
            for field in ("openai_api_key", "personal_access_token", "bedrock_api_key")
        )
        if not has_token and not has_key:
            raise ConfigError("Codex auth file does not contain a supported Codex credential")
        return AuthMetadata(
            account_id=_safe_str(token_data.get("account_id")),
            auth_mode=_safe_str(data.get("auth_mode")),
            fingerprint=_fingerprint(text),
            access_token=_safe_str(token_data.get("access_token")),
        )

    def fetch_usage(
        self,
        auth_text: str,
        timeout_s: float,
    ) -> dict[str, Any] | str | UsageFetchError:
        return fetch_openai_usage(
            auth_text,
            timeout_s,
            self.metadata_from_text,
            "claude-swap/codex-openai-usage",
        )

    def access_token_expired(self, auth_text: str) -> bool:
        """Whether the stored access token is expired or near expiry.

        No tokens dict means an API-key-only account: nothing to refresh,
        never expired. A tokens dict with a missing or unverifiable access
        token is treated as expired so the refresh path gets a chance to
        mint a usable one.
        """
        try:
            data = json.loads(auth_text)
        except json.JSONDecodeError:
            return False
        if not isinstance(data, dict):
            return False
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            return False
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            return True
        exp = _jwt_exp_seconds(access_token)
        if exp is None:
            return True
        now = datetime.now(timezone.utc).timestamp()
        return now + ACCESS_TOKEN_EXPIRY_BUFFER_S >= exp

    def refresh_auth(self, auth_text: str, timeout_s: float) -> RefreshResult:
        """Refresh an expired Codex access token via the OpenAI token endpoint.

        Mirrors codex-rs/login/src/auth/manager.rs (rust-v0.143.0): same client
        id, endpoint, and update-if-present semantics for the rotated fields
        (all three are Optional upstream). Every other field in the file
        (auth_mode, agent_identity, tokens.account_id, unknown keys) is
        preserved untouched.
        """
        try:
            data = json.loads(auth_text)
        except json.JSONDecodeError:
            return RefreshResult(None, "no_refresh_token")
        if not isinstance(data, dict):
            return RefreshResult(None, "no_refresh_token")
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            return RefreshResult(None, "no_refresh_token")
        refresh_token = tokens.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return RefreshResult(None, "no_refresh_token")

        body = json.dumps(
            {
                "client_id": CODEX_OAUTH_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "openid profile email",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            CODEX_REFRESH_TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "claude-swap/codex-openai-refresh",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            resp_body = exc.read().decode(errors="replace") if hasattr(exc, "read") else ""
            _logger.debug(
                "codex token refresh failed: status=%s body=%s", exc.code, resp_body[:500]
            )
            # Permanent only when the server itself rejected the grant: a 4xx
            # AND an explicit marker in the body. Anything ambiguous stays
            # transient - a misclassified permanent would wrongly quarantine a
            # live token (mirrors oauth.py).
            if exc.code in (400, 401, 403) and any(
                marker in resp_body
                for marker in (
                    "invalid_grant",
                    "invalid_client",
                    "refresh_token_reused",
                    "refresh_token_invalidated",
                )
            ):
                return RefreshResult(None, "invalid_grant")
            return RefreshResult(None, "transient")
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            _logger.debug("codex token refresh failed: %r", exc)
            return RefreshResult(None, "transient")

        if not isinstance(resp_data, dict):
            return RefreshResult(None, "transient")
        for field in ("access_token", "refresh_token", "id_token"):
            value = resp_data.get(field)
            if isinstance(value, str) and value:
                tokens[field] = value
        data["tokens"] = tokens
        data["last_refresh"] = (
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        return RefreshResult(json.dumps(data, indent=2) + "\n", None)
