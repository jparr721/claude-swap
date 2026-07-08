"""Tests for provider registry type foundations."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_swap.providers.types import (
    AuthMetadata,
    BackendAdapter,
    FrontendAdapter,
    ProviderDefinition,
    ProviderRef,
    UsageFetchError,
)


def test_provider_type_dataclasses_and_display_name() -> None:
    metadata = AuthMetadata(
        account_id="acct-1",
        auth_mode="oauth",
        fingerprint="fingerprint",
        access_token="token",
    )
    error = UsageFetchError(message="usage unavailable", retry_after_s=3.5)

    class _Frontend:
        provider_ref = ("codex", "openai")
        display_name = "Codex"
        login_command = "codex login"

        def active_auth_path(self) -> Path:
            return Path("/tmp/auth.json")

    class _Backend:
        backend_id = "openai"
        display_name = "OpenAI"

        def metadata_from_text(self, text: str) -> AuthMetadata:
            return metadata

        def fetch_usage(self, auth_text: str, timeout_s: float):
            return error

    definition = ProviderDefinition(
        ref=ProviderRef(frontend="codex", backend="openai"),
        frontend=_Frontend(),
        backend=_Backend(),
        state_dir=Path("/tmp/state"),
        default_label_prefix="codex-account",
    )

    assert metadata.account_id == "acct-1"
    assert error.retry_after_s == 3.5
    assert definition.display_name == "Codex / OpenAI"


def test_provider_protocols_are_importable() -> None:
    assert FrontendAdapter.__name__ == "FrontendAdapter"
    assert BackendAdapter.__name__ == "BackendAdapter"
