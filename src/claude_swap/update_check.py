"""Upgrade behavior for this distribution."""

from __future__ import annotations


def check_for_update(_current_version: str) -> str | None:
    """Return no update notification for this distribution."""
    return None


def run_self_upgrade() -> int:
    """Report that package-manager self-upgrades are disabled."""
    from claude_swap.printer import error

    error(
        "Self-upgrade is disabled for this distribution. "
        "Update it from the source you installed from."
    )
    return 1
