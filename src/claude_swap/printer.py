"""Console output formatting for Claude Swap.

A single rich Theme carries the palette (warm terracotta accent, soft-gray
muted, dim tertiary); the string helpers style through it so every surface
renders identically. rich owns terminal/color detection (NO_COLOR,
FORCE_COLOR, TTY, Windows VT) - falls back to plain text when colors are
not supported.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

THEME = Theme(
    {
        "accent": "color(173)",
        "muted": "color(250)",
        "dim": "dim",
        "warn": "yellow",
        "err": "red",
    }
)

console = Console(theme=THEME, highlight=False)
err_console = Console(theme=THEME, stderr=True, highlight=False)


def force_utf8_output() -> None:
    """Make stdout/stderr encode UTF-8 so ● → ├ ─ └ don't crash on a legacy
    console (cp1252 on Windows, or an ASCII/C locale). errors="replace" keeps
    output flowing on any stream that still can't render a glyph. No-op where
    the stream can't be reconfigured (replaced/captured streams in tests)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _styled(text: str, style: str) -> str:
    """Render text with a theme style to a string (ANSI only when the
    console's color decision allows it). soft_wrap keeps single-line
    helpers single-line regardless of terminal width."""
    with console.capture() as capture:
        console.print(Text(text, style=style), end="", soft_wrap=True)
    return capture.get()


# --- Inline stylers (return styled strings for composing lines) ---


def accent(text: str) -> str:
    """Warm accent color for important elements."""
    return _styled(text, "accent")


def muted(text: str) -> str:
    """Slightly dimmer than normal -- for usage stats, org tags."""
    return _styled(text, "muted")


def dimmed(text: str) -> str:
    """Dim for tertiary info -- hints, secondary detail."""
    return _styled(text, "dim")


def bolded(text: str) -> str:
    """Bold (no color) for structure."""
    return _styled(text, "bold")


def bold_accent(text: str) -> str:
    """Bold + accent for key markers like (active)."""
    return _styled(text, "bold accent")


def yellowed(text: str) -> str:
    """Yellow for warning-toned text (string form; ``warning()`` prints)."""
    return _styled(text, "warn")


# --- Line printers (call print() internally) ---


def error(msg: str) -> None:
    """Print an error message (red) to stderr."""
    err_console.print(Text(msg, style="err"), soft_wrap=True)


def warning(msg: str) -> None:
    """Print a warning message (yellow)."""
    console.print(Text(msg, style="warn"), soft_wrap=True)


# --- Display helpers for process detection ---

_ENTRYPOINT_LABELS: dict[str, str] = {
    "cli": "CLI",
    "claude-vscode": "VS Code",
    "claude-desktop": "Desktop",
    "sdk-cli": "SDK",
    "sdk-ts": "SDK",
    "sdk-py": "SDK",
    "mcp": "MCP",
    "local-agent": "Agent",
    "remote": "Remote",
}

_IDE_SHORT_NAMES: dict[str, str] = {
    "Visual Studio Code": "VS Code",
}


def entrypoint_label(entrypoint: str) -> str:
    """Return a human-readable label for a Claude Code entrypoint."""
    return _ENTRYPOINT_LABELS.get(entrypoint, entrypoint)


def ide_short_name(ide_name: str) -> str:
    """Return a short display name for an IDE."""
    return _IDE_SHORT_NAMES.get(ide_name, ide_name)


def abbreviate_path(path: str) -> str:
    """Replace the user's home directory prefix with ~."""
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def format_age(started_at_ms: int) -> str:
    """Format a millisecond epoch timestamp as a human-readable age."""
    elapsed = int(time.time()) - (started_at_ms // 1000)
    if elapsed < 60:
        return "just now"
    if elapsed < 3600:
        return f"{elapsed // 60}m ago"
    if elapsed < 86400:
        return f"{elapsed // 3600}h ago"
    return f"{elapsed // 86400}d ago"
