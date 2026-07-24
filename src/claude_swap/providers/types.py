"""Shared provider adapter types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from claude_swap.usage_store import UsageEntry


@dataclass(frozen=True)
class AuthMetadata:
    account_id: str
    auth_mode: str
    fingerprint: str
    access_token: str


@dataclass(frozen=True)
class UsageFetchError:
    message: str
    retry_after_s: float | None
    status_code: int


@dataclass(frozen=True)
class RefreshResult:
    """Result of a refresh-token grant attempt (matches oauth.RefreshOutcome).

    - error is None: success, auth_text is the full rotated auth JSON
    - "invalid_grant": the token endpoint rejected the grant; re-login required
    - "no_refresh_token": no usable refresh token in the auth (or backend
      does not support refresh)
    - "transient": network/server error; the token may still be valid
    """

    auth_text: str | None
    error: str | None


@dataclass(frozen=True)
class ProviderRef:
    frontend: str
    backend: str


class FrontendAdapter(Protocol):
    provider_ref: ProviderRef
    display_name: str
    login_command: str

    def active_auth_path(self) -> Path:
        raise NotImplementedError

    def headless_login_argv(self) -> list[str] | None:
        raise NotImplementedError


class BackendAdapter(Protocol):
    backend_id: str
    display_name: str

    def metadata_from_text(self, text: str) -> AuthMetadata:
        raise NotImplementedError

    def fetch_usage(
        self, auth_text: str, timeout_s: float
    ) -> dict[str, Any] | str | UsageFetchError:
        raise NotImplementedError

    def access_token_expired(self, auth_text: str) -> bool:
        raise NotImplementedError

    def refresh_auth(self, auth_text: str, timeout_s: float) -> RefreshResult:
        raise NotImplementedError


@dataclass(frozen=True)
class ProviderDefinition:
    ref: ProviderRef
    frontend: FrontendAdapter
    backend: BackendAdapter
    state_dir: Path
    default_label_prefix: str

    @property
    def display_name(self) -> str:
        return f"{self.frontend.display_name} / {self.backend.display_name}"


ProviderFactory = Callable[[], ProviderDefinition]


@dataclass(frozen=True)
class ProviderAccountRow:
    """Display-grade row for one managed provider account."""

    number: str
    label: str
    is_active: bool
    usage: UsageEntry
