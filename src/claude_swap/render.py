"""Rich presentation for the CLI's human output.

Pure presentation: consumes display dataclasses and UsageEntry objects,
renders through printer's shared theme. No file, network, or store access -
data gathering (including process detection) stays with the callers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Group, RenderableType
from rich.progress_bar import ProgressBar
from rich.style import Style
from rich.table import Table
from rich.text import Text

from claude_swap import oauth
from claude_swap.json_output import (
    USAGE_API_KEY,
    USAGE_KEYCHAIN_UNAVAILABLE,
    USAGE_RELOGIN_REQUIRED,
    USAGE_TOKEN_EXPIRED,
)
from claude_swap.models import ClaudeListData, ClaudeStatusData, FetchProgress
from claude_swap.printer import (
    abbreviate_path,
    console,
    entrypoint_label,
    err_console,
    format_age,
    ide_short_name,
)
from claude_swap.providers.types import ProviderAccountRow
from claude_swap.usage_store import UsageEntry

# Age past which a served last-good measurement is annotated (matches the
# usage store's staleness note threshold used by the old inline renderers).
_USAGE_AGE_NOTE_S = 90.0
_BAR_WIDTH = 10

# Theme colors resolved once through printer's themed console. Tables/cells
# built here get handed both to the CLI's themed console and (in tests)
# directly to a bare rich Console, which knows nothing of THEME's custom
# names ("muted"/"accent"/"warn") -- resolving to a concrete Style up front
# means the color survives regardless of which console does the printing.
_MUTED = console.get_style("muted")
_ACCENT = console.get_style("accent")
_WARN = console.get_style("warn")
_BOLD_ACCENT = Style(bold=True) + _ACCENT

# Human notes for sentinel usage states (fallback: the raw sentinel string).
SENTINEL_NOTES = {
    USAGE_TOKEN_EXPIRED: "token expired - Claude Code refreshes the active account",
    USAGE_API_KEY: "API key (no quota)",
    USAGE_KEYCHAIN_UNAVAILABLE: "keychain unavailable - locked or in use; try again",
    # The hard line break before "then run:" keeps the actionable command on
    # its own short line: this note is long enough that Rich's word-wrap
    # point (which shifts with the neighboring Account column's width) can
    # otherwise land mid-command, splitting "cswap claude add"
    # across two lines.
    USAGE_RELOGIN_REQUIRED: (
        "re-login needed - refresh token dead; log in with Claude Code,\n"
        "then run: cswap claude add"
    ),
}


def last_seen_note(entry: UsageEntry) -> str | None:
    """"last seen 53% used - 12m ago" from an entry's last-good measurement."""
    if entry.last_good is None or entry.fetched_at is None:
        return None
    headroom = oauth.account_headroom(entry.last_good)
    if headroom is None:
        return None
    return (
        f"last seen {100 - headroom:.0f}% used - "
        f"{format_age(int(entry.fetched_at * 1000))}"
    )


def _bar(pct: float) -> ProgressBar:
    return ProgressBar(total=100, completed=min(100.0, max(0.0, pct)), width=_BAR_WIDTH)


def _window_row(grid: Table, label: str, window: dict, marker: str) -> None:
    detail = f"{window['pct']:>3.0f}%{marker}"
    cell = oauth.fresh_reset_strings(window)
    if cell is not None:
        countdown, clock = cell
        detail = f"{detail}  resets {clock} in {countdown}"
    grid.add_row(Text(label, style=_MUTED), _bar(window["pct"]), Text(detail, style=_MUTED))


def _age_suffix(entry: UsageEntry) -> str | None:
    if (
        entry.age_s is not None
        and entry.age_s > _USAGE_AGE_NOTE_S
        and entry.fetched_at is not None
    ):
        return format_age(int(entry.fetched_at * 1000))
    return None


def _usage_grid() -> Table:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column()
    return grid


def _sentinel_cell(entry: UsageEntry, note: str) -> RenderableType:
    parts: list[RenderableType] = [Text(note, style="dim")]
    seen = last_seen_note(entry)
    if seen is not None and entry.sentinel != USAGE_API_KEY:
        parts.append(Text(seen, style=_MUTED))
    return Group(*parts)


def _unavailable_cell(entry: UsageEntry) -> RenderableType:
    detail = "usage unavailable"
    if entry.last_error:
        detail += f" ({entry.last_error})"
    return Text(detail, style="dim")


def claude_usage_cell(entry: UsageEntry) -> RenderableType:
    """Bars/sentinels/age for one Claude account (spend, 5h/7d, scoped models)."""
    if entry.sentinel is not None:
        return _sentinel_cell(entry, SENTINEL_NOTES.get(entry.sentinel, entry.sentinel))
    if entry.last_good is None:
        return _unavailable_cell(entry)
    usage = entry.last_good
    grid = _usage_grid()
    spend = usage.get("spend")
    if spend:
        detail = f"{spend['pct']:>3.0f}%  ${spend['used']:,.2f} / ${spend['limit']:,.2f}"
        cell = oauth.fresh_reset_strings(spend)
        if cell is not None:
            detail = f"{detail}  resets {cell[1]}"
        grid.add_row(Text("$$", style=_MUTED), _bar(spend["pct"]), Text(detail, style=_MUTED))
    for label, window in (("5h", usage.get("five_hour")), ("7d", usage.get("seven_day"))):
        if window:
            _window_row(grid, label, window, "")
    for window in usage.get("scoped") or []:
        marker = "  (!)" if window["pct"] >= 100 else ""
        _window_row(grid, window["name"], window, marker)
    parts: list[RenderableType] = [grid]
    age = _age_suffix(entry)
    if age is not None:
        parts.append(Text(age, style="dim"))
    return Group(*parts)


def provider_usage_cell(entry: UsageEntry, relogin_hint: str) -> RenderableType:
    """Bars/sentinels/age for one provider account (windows list shape)."""
    if entry.sentinel == USAGE_RELOGIN_REQUIRED:
        return Text(relogin_hint, style=_WARN)
    if entry.sentinel is not None:
        return Text(SENTINEL_NOTES.get(entry.sentinel, entry.sentinel), style="dim")
    if entry.last_good is None:
        return _unavailable_cell(entry)
    usage = entry.last_good
    grid = _usage_grid()
    for window in usage.get("windows") or []:
        _window_row(grid, str(window.get("label", "?")), window, "")
    credits = usage.get("credits")
    if isinstance(credits, (int, float)):
        grid.add_row(Text("Credits", style=_MUTED), Text(""), Text(f"{credits:g}", style=_MUTED))
    parts: list[RenderableType] = [grid]
    age = _age_suffix(entry)
    if age is not None:
        parts.append(Text(age, style="dim"))
    return Group(*parts)


def _account_text(
    name: str, tag: str, is_active: bool, alias: str = "", disabled: bool = False
) -> Text:
    text = Text(alias) if alias else Text(name)
    if alias:
        text.append(f" ({name})", style=_MUTED)
    if tag:
        text.append(f" [{tag}]", style=_MUTED)
    if is_active:
        text.append(" *", style=_BOLD_ACCENT)
    if disabled:
        text.append(" (disabled)", style="dim")
    return text


def claude_accounts_table(data: ClaudeListData) -> Table:
    table = Table(title="Claude Code", title_justify="left", title_style="bold")
    table.add_column("#", justify="right", style=_MUTED)
    table.add_column("Account")
    table.add_column("Usage")
    for row in data.rows:
        cell: RenderableType = claude_usage_cell(row.usage)
        if row.token_status:
            cell = Group(cell, Text(row.token_status, style="dim"))
        table.add_row(
            row.number,
            _account_text(
                row.email, row.tag, row.is_active, row.alias, row.disabled
            ),
            cell,
        )
    return table


def provider_accounts_table(
    display_name: str, rows: list[ProviderAccountRow], relogin_hint: str
) -> Table:
    table = Table(title=display_name, title_justify="left", title_style="bold")
    table.add_column("#", justify="right", style=_MUTED)
    table.add_column("Account")
    table.add_column("Plan")
    table.add_column("Usage")
    for row in rows:
        plan = "-"
        if row.usage.last_good is not None:
            plan = str(row.usage.last_good.get("plan") or "-")
        table.add_row(
            row.number,
            _account_text(row.label, "", row.is_active),
            Text(plan, style=_MUTED),
            provider_usage_cell(row.usage, relogin_hint),
        )
    return table


def running_instances(sessions, ide_instances) -> RenderableType | None:
    """The 'Running instances' block; None when nothing is running."""
    if not sessions and not ide_instances:
        return None
    groups: dict[tuple[str, str], dict[str, int]] = {}
    for session in sessions:
        key = (entrypoint_label(session.entrypoint), abbreviate_path(session.cwd))
        groups.setdefault(key, {"sessions": 0, "ide": 0})["sessions"] += 1
    for ide in ide_instances:
        name = ide_short_name(ide.ide_name)
        for folder in ide.workspace_folders:
            key = (name, abbreviate_path(folder))
            groups.setdefault(key, {"sessions": 0, "ide": 0})["ide"] += 1
    lines: list[RenderableType] = [Text("Running instances:", style="bold")]
    for (label, cwd), counts in groups.items():
        parts = []
        if counts["sessions"]:
            s = counts["sessions"]
            parts.append(f"{s} session{'s' if s > 1 else ''}")
        if counts["ide"]:
            parts.append("IDE")
        line = Text("  ")
        line.append(label, style=_MUTED)
        line.append(f"   {cwd}  ", style=_MUTED)
        line.append(f"({', '.join(parts)})", style="dim")
        lines.append(line)
    return Group(*lines)


def claude_status(data: ClaudeStatusData) -> None:
    if data.email is None:
        console.print(Text.assemble(("Status: ", "bold"), ("No active Claude account", "dim")))
        return
    if data.account_number is None:
        console.print(
            Text.assemble(("Status: ", "bold"), (data.email, ""), (" (not managed)", "dim"))
        )
        return
    line = Text.assemble(
        ("Status: ", "bold"), (f"Account-{data.account_number}", _ACCENT), (f" ({data.email}", "")
    )
    if data.tag:
        line.append(f" [{data.tag}]", style=_MUTED)
    line.append(")")
    console.print(line)
    console.print(Text(f"  Total managed accounts: {data.total_accounts}", style="dim"))
    if data.usage is not None:
        console.print(claude_usage_cell(data.usage))


def provider_status(display_name: str, payload: dict) -> None:
    active = payload.get("active")
    prefix = Text(f"{display_name} status: ", style="bold")
    if active is None:
        console.print(Text.assemble(prefix, (f"No active {display_name} auth", "dim")))
    elif active.get("managed"):
        console.print(
            Text.assemble(
                prefix,
                (f"Account-{active['number']}", _ACCENT),
                (f" ({active.get('label', '')})", ""),
            )
        )
    else:
        console.print(Text.assemble(prefix, ("(not managed)", _MUTED)))


@contextmanager
def fetch_progress(provider_label: str) -> Iterator[FetchProgress]:
    """Transient stderr spinner with live per-account counts.

    A no-op callback when stderr is not a terminal (pipes, CI, CliRunner),
    so machine consumers and tests never see spinner frames.
    """
    if not err_console.is_terminal:
        yield lambda done, total, label: None
        return
    with err_console.status(f"Fetching {provider_label} usage") as status:

        def tick(done: int, total: int, label: str) -> None:
            status.update(f"Fetching {provider_label} usage {done}/{total} - {label}")

        yield tick
