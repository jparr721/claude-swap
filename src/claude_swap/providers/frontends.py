"""Frontend auth file adapters."""

from __future__ import annotations

from pathlib import Path

from claude_swap.paths import get_codex_auth_path, get_opencode_auth_path
from claude_swap.providers.types import ProviderRef


class CodexFrontend:
    provider_ref = ProviderRef("codex", "openai")
    display_name = "Codex"
    login_command = "codex login"

    def active_auth_path(self) -> Path:
        return get_codex_auth_path()


class OpencodeFrontend:
    provider_ref = ProviderRef("opencode", "openai")
    display_name = "opencode"
    login_command = "opencode auth login"

    def active_auth_path(self) -> Path:
        return get_opencode_auth_path()
