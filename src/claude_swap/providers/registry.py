"""Provider registry."""

from __future__ import annotations

from claude_swap.paths import get_provider_store_root
from claude_swap.providers.frontends import CodexFrontend, OpencodeFrontend
from claude_swap.providers.openai import CodexOpenAIBackend, OpencodeOpenAIBackend
from claude_swap.providers.store import ProviderAccountStore
from claude_swap.providers.types import ProviderDefinition, ProviderRef


def _codex_openai_definition() -> ProviderDefinition:
    ref = ProviderRef(frontend="codex", backend="openai")
    return ProviderDefinition(
        ref=ref,
        frontend=CodexFrontend(),
        backend=CodexOpenAIBackend(),
        state_dir=get_provider_store_root(ref.frontend, ref.backend),
        default_label_prefix="codex-openai-account",
        switch_mode="symlink",
    )


def _opencode_openai_definition() -> ProviderDefinition:
    ref = ProviderRef(frontend="opencode", backend="openai")
    return ProviderDefinition(
        ref=ref,
        frontend=OpencodeFrontend(),
        backend=OpencodeOpenAIBackend(),
        state_dir=get_provider_store_root(ref.frontend, ref.backend),
        default_label_prefix="opencode-openai-account",
        switch_mode="snapshot-refused",
    )


def provider_definitions() -> list[ProviderDefinition]:
    return [
        _codex_openai_definition(),
        _opencode_openai_definition(),
    ]


def get_provider(frontend: str, backend: str) -> ProviderAccountStore:
    for definition in provider_definitions():
        if definition.ref.frontend == frontend and definition.ref.backend == backend:
            return ProviderAccountStore(definition)
    raise KeyError(f"Unknown provider: {frontend}/{backend}")


def managed_aggregate_providers() -> list[ProviderAccountStore]:
    return [
        get_provider("codex", "openai"),
        get_provider("opencode", "openai"),
    ]
