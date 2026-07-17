from __future__ import annotations

import pytest

from claude_swap.providers.registry import (
    get_provider,
    managed_aggregate_providers,
    provider_definitions,
)


def test_registry_contains_only_codex_openai() -> None:
    keys = {
        (definition.ref.frontend, definition.ref.backend)
        for definition in provider_definitions()
    }

    assert keys == {("codex", "openai")}


def test_get_provider_returns_store_for_codex_openai() -> None:
    store = get_provider("codex", "openai")

    assert store.definition.ref.frontend == "codex"
    assert store.definition.ref.backend == "openai"
    assert store.definition.default_label_prefix == "codex-openai-account"


def test_get_provider_rejects_unknown_backend() -> None:
    with pytest.raises(KeyError, match="Unknown provider: codex/anthropic"):
        get_provider("codex", "anthropic")


def test_codex_definition_uses_device_login() -> None:
    codex = get_provider("codex", "openai")

    assert codex.definition.frontend.headless_login_argv() == [
        "codex",
        "login",
        "--device-auth",
    ]


def test_managed_aggregate_providers_returns_codex_store() -> None:
    providers = managed_aggregate_providers()

    assert [(store.definition.ref.frontend, store.definition.ref.backend) for store in providers] == [
        ("codex", "openai"),
    ]
