"""Shared provider adapter types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


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


@dataclass(frozen=True)
class ProviderDefinition:
    ref: ProviderRef
    frontend: FrontendAdapter
    backend: BackendAdapter
    state_dir: Path
    default_label_prefix: str
    switch_mode: str

    @property
    def display_name(self) -> str:
        return f"{self.frontend.display_name} / {self.backend.display_name}"


ProviderFactory = Callable[[], ProviderDefinition]
