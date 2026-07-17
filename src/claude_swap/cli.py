"""Command-line interface for Claude Swap."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from enum import Enum
from types import SimpleNamespace

import typer

from claude_swap import __version__, render
from claude_swap.exceptions import ClaudeSwitchError, ConfigError
from claude_swap.json_output import error_envelope, provider_envelope
from claude_swap.printer import dimmed, error, force_utf8_output, muted
from claude_swap.process_detection import get_running_instances
from claude_swap.providers.registry import (
    get_provider,
    managed_aggregate_providers,
)
from claude_swap.providers.store import ProviderAccountStore
from claude_swap.providers.types import ProviderAccountRow
from claude_swap.switcher import ClaudeAccountSwitcher

_logger = logging.getLogger("claude-swap")

app = typer.Typer(
    help="Multi-Account Switcher for Claude Code",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        print(f"{_prog_name()} {__version__}")
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit",
    ),
) -> None:
    """Multi-Account Switcher for Claude Code."""


def _init_switcher(debug: bool) -> ClaudeAccountSwitcher:
    """Construct the switcher and enforce the root-user guard (POSIX only)."""
    switcher = ClaudeAccountSwitcher(debug=debug)
    if sys.platform != "win32":
        if os.geteuid() == 0 and not switcher._is_running_in_container():
            error("Error: Do not run this script as root (unless running in a container)")
            raise typer.Exit(1)
    return switcher


def _update_check_note() -> None:
    from claude_swap.update_check import check_for_update

    msg = check_for_update(__version__)
    if msg:
        print(f"\n{muted(msg)}", file=sys.stderr)


def _dispatch(
    action: Callable[[], dict | None], json_mode: bool, update_check: bool
) -> None:
    """Run a command body under the standard error contract.

    JSON mode keeps stdout pure: handled errors emit the structured envelope
    there (exit 1) and the Ctrl-C note goes to stderr (exit 130). The CLI is
    the single serialization point - command bodies return payload dicts and
    never print JSON themselves.
    """
    try:
        payload = action()
    except ClaudeSwitchError as e:
        if json_mode:
            print(json.dumps(error_envelope(e), indent=2))
        else:
            error(f"Error: {e}")
        raise typer.Exit(1) from e
    except KeyboardInterrupt:
        print(
            f"\n{dimmed('Operation cancelled')}",
            file=sys.stderr if json_mode else sys.stdout,
        )
        raise typer.Exit(130) from None
    if json_mode and payload is not None:
        print(json.dumps(payload, indent=2))
    if update_check and not json_mode:
        _update_check_note()


def _render_claude_accounts(switcher: ClaudeAccountSwitcher, token_status: bool) -> None:
    with render.fetch_progress("Claude") as tick:
        data = switcher.list_data(show_token_status=token_status, on_fetch=tick)
    if data.first_run_needed:
        render.console.print("No accounts are managed yet.", style="dim")
        switcher.first_run_setup()
        return
    render.console.print(render.claude_accounts_table(data))
    # Running instances are presentation-adjacent system state; failures to
    # detect them must never break the listing.
    try:
        sessions, ide_instances = get_running_instances()
    except Exception:
        _logger.debug("Failed to detect running instances", exc_info=True)
        return
    block = render.running_instances(sessions, ide_instances)
    if block is not None:
        render.console.print(block)


def _provider_relogin_hint(provider_store: ProviderAccountStore) -> str:
    ref = provider_store.definition.ref
    return f"re-login needed - re-add with: cswap {ref.frontend} add"


def _fetch_provider_rows(provider_store: ProviderAccountStore) -> list[ProviderAccountRow]:
    """One usage collection per provider per invocation, under its spinner."""
    with render.fetch_progress(provider_store.definition.display_name) as tick:
        return provider_store.list_data(on_fetch=tick)


def _aggregate_list(
    switcher: ClaudeAccountSwitcher, json_mode: bool, token_status: bool
) -> dict | None:
    """Claude listing plus every managed provider's section.

    Provider sections are auxiliary: their state living in separate trees must
    never fail the primary Claude listing. In JSON mode returns the schema-v2
    provider envelope with per-provider errors embedded; providers with no
    accounts are skipped silently in the human view, matching the old aggregate.
    """
    if not json_mode:
        _render_claude_accounts(switcher, token_status)
        for provider_store in managed_aggregate_providers():
            try:
                rows = _fetch_provider_rows(provider_store)
                if rows:
                    print()
                    render.console.print(
                        render.provider_accounts_table(
                            provider_store.definition.display_name,
                            rows,
                            _provider_relogin_hint(provider_store),
                        )
                    )
            except ClaudeSwitchError as provider_err:
                print(
                    dimmed(
                        f"{provider_store.definition.frontend.display_name} "
                        f"accounts unavailable: {provider_err}"
                    ),
                    file=sys.stderr,
                )
        return None

    payload = switcher.list_accounts()
    provider_payloads: dict[str, dict[str, dict]] = {"claude": {"default": payload or {}}}
    for provider_store in managed_aggregate_providers():
        provider_ref = provider_store.definition.ref
        try:
            provider_payload = provider_store.list_accounts()
            provider_payloads.setdefault(provider_ref.frontend, {})[
                provider_ref.backend
            ] = provider_payload or {}
        except ClaudeSwitchError as provider_err:
            provider_payloads.setdefault(provider_ref.frontend, {})[
                provider_ref.backend
            ] = {
                "error": {
                    "type": provider_err.__class__.__name__,
                    "message": str(provider_err),
                }
            }
    return provider_envelope(provider_payloads)


@app.command("ls")
def ls_command(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Read-only account overview across all providers."""
    _dispatch(
        lambda: _aggregate_list(
            _init_switcher(debug), json_mode=json_output, token_status=False
        ),
        json_mode=json_output,
        update_check=True,
    )


def _run_upgrade() -> None:
    # Self-upgrade runs before switcher init so we don't touch config/keychain
    # just to upgrade the tool itself.
    from claude_swap.update_check import run_self_upgrade

    try:
        raise typer.Exit(run_self_upgrade())
    except KeyboardInterrupt:
        print(f"\n{dimmed('Upgrade cancelled')}")
        raise typer.Exit(130) from None


@app.command("upgrade")
def upgrade_command() -> None:
    """Self-upgrade claude-swap to the latest release."""
    _run_upgrade()


@app.command("update", hidden=True)
def update_command() -> None:
    """Alias of upgrade."""
    _run_upgrade()


@app.command("purge")
def purge_command(
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Remove all claude-swap data from this machine."""

    def action() -> None:
        _init_switcher(debug).purge()

    _dispatch(action, json_mode=False, update_check=False)


config_app = typer.Typer(
    help="Read and edit claude-swap settings (settings.json in the backup root)."
)
app.add_typer(config_app, name="config")


def _config_list_body(json_mode: bool, debug: bool) -> None:
    from claude_swap.settings import effective_settings, format_setting_value, settings_path

    def action() -> None:
        root = _init_switcher(debug).backup_dir
        rows = effective_settings(root)
        if json_mode:
            print(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "path": str(settings_path(root)),
                        "settings": [
                            {"key": spec.dotted, "value": value, "isSet": is_set}
                            for spec, value, is_set in rows
                        ],
                    },
                    indent=2,
                )
            )
        else:
            key_w = max(len(spec.dotted) for spec, _, _ in rows)
            val_w = max(len(format_setting_value(v)) for _, v, _ in rows)
            for spec, value, is_set in rows:
                line = f"{spec.dotted:<{key_w}}  {format_setting_value(value):<{val_w}}"
                print(line if is_set else f"{line}  {dimmed('(default)')}")

    _dispatch(action, json_mode=json_mode, update_check=False)


def _merged_config_flags(
    ctx: typer.Context, json_output: bool, debug: bool
) -> tuple[bool, bool]:
    """Fold group-level `cswap config --json/--debug` into the subcommand's flags."""
    parent = ctx.obj or {}
    return (
        json_output or bool(parent.get("json", False)),
        debug or bool(parent.get("debug", False)),
    )


def _reject_group_json(ctx: typer.Context) -> None:
    """Non-JSON subcommands must fail loudly on a pre-verb --json, not drop it."""
    if (ctx.obj or {}).get("json"):
        raise typer.BadParameter("--json can only be used with list or get")


@config_app.callback(invoke_without_command=True)
def config_main(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Read and edit claude-swap settings."""
    ctx.obj = {"json": json_output, "debug": debug}
    if ctx.invoked_subcommand is None:
        _config_list_body(json_output, debug)


@config_app.command("list")
def config_list(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Show all effective settings (the default)."""
    json_output, debug = _merged_config_flags(ctx, json_output, debug)
    _config_list_body(json_output, debug)


@config_app.command("get")
def config_get(
    ctx: typer.Context,
    key: str = typer.Argument(..., metavar="KEY"),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Print one setting's effective value."""
    from claude_swap.settings import effective_settings, format_setting_value, setting_spec

    json_output, debug = _merged_config_flags(ctx, json_output, debug)

    def action() -> None:
        root = _init_switcher(debug).backup_dir
        spec = setting_spec(key)
        value, is_set = next((v, s) for sp, v, s in effective_settings(root) if sp is spec)
        if json_output:
            print(
                json.dumps(
                    {"schemaVersion": 1, "key": spec.dotted, "value": value, "isSet": is_set},
                    indent=2,
                )
            )
        else:
            print(format_setting_value(value))

    _dispatch(action, json_mode=json_output, update_check=False)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(..., metavar="KEY"),
    value: str = typer.Argument(..., metavar="VALUE"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Validate and persist one setting."""
    from claude_swap.settings import format_setting_value, set_setting

    _reject_group_json(ctx)
    _, debug = _merged_config_flags(ctx, False, debug)

    def action() -> None:
        stored = set_setting(_init_switcher(debug).backup_dir, key, value)
        print(f"{key} = {format_setting_value(stored)}")

    _dispatch(action, json_mode=False, update_check=False)


@config_app.command("unset")
def config_unset(
    ctx: typer.Context,
    key: str = typer.Argument(..., metavar="KEY"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Remove one setting (revert to the default)."""
    from claude_swap.settings import format_setting_value, setting_spec, unset_setting

    _reject_group_json(ctx)
    _, debug = _merged_config_flags(ctx, False, debug)

    def action() -> None:
        if unset_setting(_init_switcher(debug).backup_dir, key):
            default = setting_spec(key).default
            print(f"{key} unset (default: {format_setting_value(default)})")
        else:
            print(muted(f"{key} is not set; nothing to do"), file=sys.stderr)

    _dispatch(action, json_mode=False, update_check=False)


@config_app.command("path")
def config_path(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Print the settings.json location."""
    from claude_swap.settings import settings_path

    _reject_group_json(ctx)
    _, debug = _merged_config_flags(ctx, False, debug)

    def action() -> None:
        print(settings_path(_init_switcher(debug).backup_dir))

    _dispatch(action, json_mode=False, update_check=False)


claude_app = typer.Typer(no_args_is_help=True, help="Claude Code accounts (Anthropic)")
app.add_typer(claude_app, name="claude")


class SwitchStrategy(str, Enum):
    best = "best"
    next_available = "next-available"


@claude_app.command("list")
def claude_list(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
    token_status: bool = typer.Option(
        False, "--token-status", help="Show OAuth token expiry state"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """List managed Claude accounts."""
    if json_output and token_status:
        # Token status is not part of the JSON v1 schema; reject rather than
        # silently ignore it (a future additive field can add it).
        raise typer.BadParameter("--token-status cannot be combined with --json")

    def action() -> dict | None:
        switcher = _init_switcher(debug)
        if json_output:
            return switcher.list_accounts()
        _render_claude_accounts(switcher, token_status)
        return None

    _dispatch(action, json_mode=json_output, update_check=True)


@claude_app.command("status")
def claude_status(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Show the current Claude account."""

    def action() -> dict | None:
        switcher = _init_switcher(debug)
        if json_output:
            return switcher.status()
        render.claude_status(switcher.status_data())
        return None

    _dispatch(action, json_mode=json_output, update_check=True)


@claude_app.command("add")
def claude_add(
    slot: int | None = typer.Option(
        None, "--slot", metavar="NUM", help="Store in a specific slot"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Add the currently logged-in Claude account."""
    _dispatch(
        lambda: _init_switcher(debug).add_account(slot=slot),
        json_mode=False,
        update_check=True,
    )


@claude_app.command("add-token")
def claude_add_token(
    token: str = typer.Argument(
        "", metavar="[TOKEN|-]", help="Setup token or API key ('-' or empty reads stdin/prompt)"
    ),
    email: str | None = typer.Option(
        None,
        "--email",
        metavar="EMAIL",
        help="Email for the account (defaults to a token.local placeholder)",
    ),
    slot: int | None = typer.Option(
        None, "--slot", metavar="NUM", help="Store in a specific slot"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Register a setup-token or API key as a managed account."""
    _dispatch(
        lambda: _init_switcher(debug).add_account_from_token(
            token=token, email=email, slot=slot
        ),
        json_mode=False,
        update_check=True,
    )


@claude_app.command("switch")
def claude_switch(
    target: str | None = typer.Argument(None, metavar="[NUM|EMAIL]"),
    to: str | None = typer.Option(None, "--to", metavar="NUM|EMAIL", help="Switch target"),
    strategy: SwitchStrategy | None = typer.Option(
        None,
        "--strategy",
        help=(
            "With bare switch: 'best' jumps to the account with the most "
            "quota headroom; 'next-available' rotates, skipping exhausted accounts"
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Activate the stored credentials without backing up the current login first",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Rotate to the next Claude account, or switch to a specific one."""
    if target is not None and to is not None:
        raise typer.BadParameter("give either a positional target or --to, not both")
    resolved = target if target is not None else to
    if strategy is not None and resolved is not None:
        raise typer.BadParameter("--strategy can only be used with bare 'switch'")
    if force and resolved is None:
        raise typer.BadParameter("--force requires a target")

    def action() -> dict | None:
        switcher = _init_switcher(debug)
        if resolved is None:
            result = switcher.switch(
                strategy=strategy.value if strategy is not None else None,
                json_output=json_output,
            )
        else:
            result = switcher.switch_to(resolved, json_output=json_output, force=force)
        if not json_output:
            try:
                _render_claude_accounts(switcher, token_status=False)
            except Exception:
                _logger.debug("post-switch account render failed", exc_info=True)
                render.console.print(
                    "usage display unavailable - run: cswap claude list", style="dim"
                )
        return result

    _dispatch(action, json_mode=json_output, update_check=True)


@claude_app.command("remove")
def claude_remove(
    identifier: str = typer.Argument(..., metavar="NUM|EMAIL"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Remove a managed Claude account."""
    _dispatch(
        lambda: _init_switcher(debug).remove_account(identifier),
        json_mode=False,
        update_check=True,
    )


@claude_app.command("export")
def claude_export(
    destination: str = typer.Argument(..., metavar="PATH|-"),
    account: str | None = typer.Option(
        None, "--account", metavar="NUM|EMAIL", help="Limit export to one account"
    ),
    full: bool = typer.Option(
        False, "--full", help="Include full ~/.claude.json (default: oauthAccount only)"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Export managed accounts to a JSON file (or '-' for stdout)."""

    def action() -> None:
        from claude_swap.transfer import export_accounts

        export_accounts(_init_switcher(debug), destination, account=account, full=full)

    _dispatch(action, json_mode=False, update_check=True)


@claude_app.command("import")
def claude_import(
    source: str = typer.Argument(..., metavar="PATH|-"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing accounts"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Import accounts from a JSON file (or '-' for stdin)."""

    def action() -> None:
        from claude_swap.transfer import import_accounts

        import_accounts(_init_switcher(debug), source, force=force)

    _dispatch(action, json_mode=False, update_check=True)


@claude_app.command("run")
def claude_run(
    account: str = typer.Argument(..., metavar="NUM|EMAIL", help="Account to run"),
    claude_args: list[str] | None = typer.Argument(
        None,
        metavar="[-- CLAUDE_ARGS...]",
        help="Everything after '--' is forwarded to claude verbatim",
    ),
    no_share: bool = typer.Option(
        False,
        "--no-share",
        help=(
            "Don't share settings/keybindings/CLAUDE.md/skills/commands/agents "
            "from ~/.claude into the session profile"
        ),
    ),
    share_history: bool = typer.Option(
        False,
        "--share-history/--no-share-history",
        help=(
            "Share conversation history from ~/.claude into the session "
            "profile (not supported on Windows)"
        ),
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """[EXPERIMENTAL] Launch Claude Code as a stored account, this terminal only.

    On POSIX this execs claude and never returns.
    """

    def action() -> None:
        switcher = _init_switcher(debug)
        from claude_swap.session import SessionManager

        SessionManager(switcher).run(
            account,
            list(claude_args or []),
            share=not no_share,
            share_history=share_history,
        )

    _dispatch(action, json_mode=False, update_check=False)


@claude_app.command("auto")
def claude_auto(
    once: bool = typer.Option(
        False, "--once", help="Evaluate once, maybe switch, and exit (exit code = outcome)"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit one machine-readable JSON event per line on stdout"
    ),
    interval: float | None = typer.Option(
        None, "--interval", metavar="SECONDS", help="Poll interval in loop mode (min 15; default 60)"
    ),
    threshold: float | None = typer.Option(
        None,
        "--threshold",
        metavar="PCT",
        help="Switch when the binding 5h/7d window reaches this utilization (50-99.9; default 90)",
    ),
    cooldown: float | None = typer.Option(
        None, "--cooldown", metavar="SECONDS", help="Minimum time between proactive switches (default 300)"
    ),
    include_api_key_accounts: bool | None = typer.Option(
        None,
        "--include-api-key-accounts/--no-include-api-key-accounts",
        help="Allow switching onto managed API-key accounts as a last resort",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Evaluate and report, but never switch or write state"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Auto-switch accounts when the active one nears its rate limit.

    Exit codes with --once: 0 switched, 1 error, 2 no action needed,
    3 blocked (no viable target / all accounts exhausted).
    """
    import signal
    import time as _time

    from claude_swap.autoswitch import AutoSwitchEngine, AutoSwitchEvent
    from claude_swap.printer import accent, yellowed
    from claude_swap.settings import load_settings, merged_with_cli

    def jsonl_emit(event: AutoSwitchEvent) -> None:
        print(json.dumps(event.to_json()), flush=True)

    def human_emit(event: AutoSwitchEvent) -> None:
        stamp = _time.strftime("%H:%M:%S")
        line = event.human()
        if event.kind == "switch":
            line = accent(line)
        elif event.kind in ("error", "account-quarantined"):
            line = yellowed(line)
        elif event.kind in ("poll", "no-switch", "sleep"):
            line = dimmed(line)
        print(f"{stamp}  {line}", flush=True)

    try:
        switcher = _init_switcher(debug)
        overrides = SimpleNamespace(
            threshold=threshold,
            interval=interval,
            cooldown=cooldown,
            include_api_key_accounts=include_api_key_accounts,
        )
        settings = merged_with_cli(load_settings(switcher.backup_dir), overrides)
        engine = AutoSwitchEngine(
            switcher,
            settings,
            jsonl_emit if json_output else human_emit,
            dry_run=dry_run,
        )

        if once:
            raise typer.Exit(engine.tick().value)

        # Loop mode: SIGTERM (systemd stop) exits the loop cleanly.
        signal.signal(signal.SIGTERM, lambda *_: engine.stop())
        if not json_output:
            print(
                dimmed(
                    f"Auto-switch running: threshold {settings.threshold:.0f}%, "
                    f"every {settings.interval_seconds:.0f}s"
                    f"{' (dry-run)' if dry_run else ''} - Ctrl-C to stop"
                )
            )
        raise typer.Exit(engine.run_loop())
    except ClaudeSwitchError as e:
        if json_output:
            print(json.dumps(error_envelope(e)))
        else:
            error(f"Error: {e}")
        raise typer.Exit(1) from e
    except KeyboardInterrupt:
        print(
            f"\n{dimmed('Auto-switch stopped')}",
            file=sys.stderr if json_output else sys.stdout,
        )
        raise typer.Exit(130) from None


codex_app = typer.Typer(no_args_is_help=True, help="Codex accounts (OpenAI)")
app.add_typer(codex_app, name="codex")


def _codex_store() -> ProviderAccountStore:
    try:
        return get_provider("codex", "openai")
    except KeyError as exc:
        raise ConfigError(str(exc)) from exc


@codex_app.command("list")
def codex_list(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
) -> None:
    """List managed Codex accounts."""

    def action() -> dict | None:
        store = _codex_store()
        if json_output:
            return store.list_accounts()
        rows = _fetch_provider_rows(store)
        if rows:
            render.console.print(
                render.provider_accounts_table(
                    store.definition.display_name,
                    rows,
                    _provider_relogin_hint(store),
                )
            )
        else:
            render.console.print(
                f"No {store.definition.display_name} accounts are managed yet.",
                style="dim",
            )
        return None

    _dispatch(action, json_mode=json_output, update_check=False)


@codex_app.command("status")
def codex_status(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
) -> None:
    """Show the active Codex account."""

    def action() -> dict | None:
        store = _codex_store()
        if json_output:
            return store.status()
        render.provider_status(store.definition.display_name, store.status())
        return None

    _dispatch(action, json_mode=json_output, update_check=False)


@codex_app.command("add")
def codex_add(
    label: str | None = typer.Option(
        None, "--label", metavar="LABEL", help="Display label for the account"
    ),
    slot: int | None = typer.Option(
        None, "--slot", metavar="NUM", help="Store in a specific slot"
    ),
) -> None:
    """Add or refresh a Codex account via device login."""
    _dispatch(
        lambda: _codex_store().add_account(label=label, slot=slot),
        json_mode=False,
        update_check=False,
    )


@codex_app.command("switch")
def codex_switch(
    target: str | None = typer.Argument(None, metavar="[NUM|LABEL]"),
    to: str | None = typer.Option(None, "--to", metavar="NUM|LABEL", help="Switch target"),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON to stdout"
    ),
) -> None:
    """Rotate to the next Codex account, or switch to a specific one."""
    if target is not None and to is not None:
        raise typer.BadParameter("give either a positional target or --to, not both")
    resolved = target if target is not None else to
    _dispatch(
        lambda: _codex_store().switch(resolved, json_output=json_output),
        json_mode=json_output,
        update_check=False,
    )


@codex_app.command("remove")
def codex_remove(
    identifier: str = typer.Argument(..., metavar="NUM|LABEL"),
) -> None:
    """Remove a managed Codex account."""
    _dispatch(
        lambda: _codex_store().remove_account(identifier),
        json_mode=False,
        update_check=False,
    )


def _prog_name() -> str:
    """The command name to show in usage/help.

    click otherwise defaults to ``os.path.basename(sys.argv[0])``, which for
    an installed entry-point shim renders as an ugly absolute path (e.g.
    ``python.exe C:\\Users\\me\\.local\\bin\\cswap``). We strip that down to the
    bare command the user typed (``cswap`` / ``claude-swap``), falling back to
    ``cswap`` for ``python -m claude_swap`` and odd launchers.
    """
    name = os.path.basename(sys.argv[0] or "")
    for ext in (".exe", ".pyw", ".py"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    if not name or name in {"__main__", "python", "python3", "py"}:
        return "cswap"
    return name


def _use_native_tls() -> None:
    """Route TLS trust decisions through the OS-native verifier.

    Claude's token endpoint (``platform.claude.com``) serves a Let's Encrypt
    chain. Python's stdlib ``ssl`` uses OpenSSL, which on Windows loads the
    system cert store as a flat set and matches CA certs by *subject name*, so a
    stale, expired duplicate of an intermediate (e.g. an old ``ISRG Root X2``
    left in the user's store) can shadow the valid path and fail verification
    with "certificate has expired" even though the served chain is valid — which
    silently breaks inactive-account token refresh. The OS-native verifiers
    (SChannel on Windows, SecureTransport on macOS) build the chain correctly
    and don't trip on the expired duplicate — the same reason Claude Code (Node,
    with its own bundled roots) is unaffected. ``truststore`` delegates to them.

    Best-effort: on any failure fall back to stdlib ``ssl`` rather than block
    the CLI over a TLS-trust nicety.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass


def main() -> None:
    """Main entry point for the CLI."""
    force_utf8_output()
    _use_native_tls()
    app(prog_name=_prog_name())


if __name__ == "__main__":
    main()
