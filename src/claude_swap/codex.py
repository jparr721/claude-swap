"""Compatibility exports for the Codex provider store."""

from __future__ import annotations

from typing import Any

from claude_swap.providers.openai import (  # noqa: F401  (usage constants re-exported for tests)
    OPENAI_USAGE_TIMEOUT_S as CODEX_USAGE_TIMEOUT_S,
    OPENAI_USAGE_URL as CODEX_USAGE_URL,
    UsageFetchError as _UsageFetchError,
)
from claude_swap.providers.registry import get_provider
from claude_swap.providers.store import ProviderAccountStore


def fetch_codex_usage(
    auth_text: str,
    timeout_s: float,
) -> dict[str, Any] | str | _UsageFetchError:
    store = get_provider("codex", "openai")
    return store.definition.backend.fetch_usage(auth_text, timeout_s)


def _codex_usage_fetch(
    auth_text: str,
    timeout_s: float,
) -> dict[str, Any] | str | _UsageFetchError:
    return fetch_codex_usage(auth_text, timeout_s)


def _codex_store() -> ProviderAccountStore:
    store = get_provider("codex", "openai")
    store.definition.backend.fetch_usage = _codex_usage_fetch  # type: ignore[method-assign]
    return store


class CodexAccountSwitcher:
    def __init__(self) -> None:
        self._store = _codex_store()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)
