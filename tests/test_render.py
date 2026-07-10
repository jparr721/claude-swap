from __future__ import annotations

from rich.console import Console

from claude_swap import render
from claude_swap.json_output import USAGE_API_KEY, USAGE_RELOGIN_REQUIRED
from claude_swap.models import ClaudeAccountRow, ClaudeListData, ClaudeStatusData
from claude_swap.providers.types import ProviderAccountRow
from claude_swap.usage_store import UsageEntry


def _rendered(renderable) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    console.print(renderable)
    return console.export_text()


def _claude_usage(pct_5h: float) -> dict:
    return {"five_hour": {"pct": pct_5h}, "seven_day": {"pct": 30.0}}


def test_claude_table_shows_accounts_and_bars() -> None:
    rows = [
        ClaudeAccountRow(
            number="1",
            email="me@example.com",
            tag="Pro",
            is_active=True,
            usage=UsageEntry(last_good=_claude_usage(78.0), fetched_at=1.0, age_s=5.0),
            token_status=None,
        ),
        ClaudeAccountRow(
            number="2",
            email="work@corp.com",
            tag="Team",
            is_active=False,
            usage=UsageEntry(),
            token_status=None,
        ),
    ]
    text = _rendered(render.claude_accounts_table(ClaudeListData(False, rows)))
    assert "me@example.com" in text
    assert "[Pro]" in text
    assert "*" in text  # active marker
    assert "78%" in text
    assert "5h" in text and "7d" in text
    assert "work@corp.com" in text
    assert "usage unavailable" in text  # no measurement at all


def test_claude_table_stale_usage_is_age_annotated() -> None:
    import time

    old = time.time() - 600
    row = ClaudeAccountRow(
        number="1",
        email="me@example.com",
        tag="Pro",
        is_active=False,
        usage=UsageEntry(last_good=_claude_usage(50.0), fetched_at=old, age_s=600.0),
        token_status=None,
    )
    text = _rendered(render.claude_accounts_table(ClaudeListData(False, [row])))
    assert "ago" in text  # display-grade affordance survives


def test_claude_table_sentinel_and_last_seen() -> None:
    import time

    row = ClaudeAccountRow(
        number="1",
        email="me@example.com",
        tag="Pro",
        is_active=False,
        usage=UsageEntry(
            sentinel=USAGE_RELOGIN_REQUIRED,
            last_good=_claude_usage(53.0),
            fetched_at=time.time() - 720,
            age_s=720.0,
        ),
        token_status=None,
    )
    text = _rendered(render.claude_accounts_table(ClaudeListData(False, [row])))
    assert "re-login needed" in text
    assert "cswap claude default add" in text
    assert "last seen" in text


def test_api_key_sentinel_has_no_last_seen_line() -> None:
    row = ClaudeAccountRow(
        number="1",
        email="key@example.com",
        tag="API",
        is_active=False,
        usage=UsageEntry(sentinel=USAGE_API_KEY, last_good=_claude_usage(10.0), fetched_at=1.0),
        token_status=None,
    )
    text = _rendered(render.claude_accounts_table(ClaudeListData(False, [row])))
    assert "API key (no quota)" in text
    assert "last seen" not in text


def test_token_status_detail_row() -> None:
    row = ClaudeAccountRow(
        number="1",
        email="me@example.com",
        tag="Pro",
        is_active=False,
        usage=UsageEntry(),
        token_status="refresh token present; access token expires in 2h",
    )
    text = _rendered(render.claude_accounts_table(ClaudeListData(False, [row])))
    assert "refresh token present" in text


def test_provider_table_plan_column_and_relogin_hint() -> None:
    rows = [
        ProviderAccountRow(
            number="1",
            label="jarred",
            is_active=True,
            usage=UsageEntry(
                last_good={"windows": [{"label": "5h", "pct": 42.0}], "plan": "pro"},
                fetched_at=1.0,
                age_s=5.0,
            ),
        ),
        ProviderAccountRow(
            number="2",
            label="personal",
            is_active=False,
            usage=UsageEntry(sentinel=USAGE_RELOGIN_REQUIRED),
        ),
    ]
    text = _rendered(
        render.provider_accounts_table(
            "Codex / OpenAI", rows, "re-login needed - re-add with: cswap codex openai add"
        )
    )
    assert "Codex / OpenAI" in text
    assert "jarred" in text and "42%" in text and "pro" in text
    assert "cswap codex openai add" in text
    assert "-" in text  # empty plan placeholder on the sentinel row


def test_claude_status_managed_prints_usage(capsys) -> None:
    render.claude_status(
        ClaudeStatusData(
            email="me@example.com",
            account_number="2",
            tag="Pro",
            total_accounts=3,
            usage=UsageEntry(last_good=_claude_usage(78.0), fetched_at=1.0, age_s=5.0),
        )
    )
    out = capsys.readouterr().out
    assert "Account-2" in out and "me@example.com" in out
    assert "Total managed accounts: 3" in out
    assert "78%" in out


def test_claude_status_unmanaged_and_absent(capsys) -> None:
    render.claude_status(
        ClaudeStatusData(email="x@y.com", account_number=None, tag="", total_accounts=0, usage=None)
    )
    assert "(not managed)" in capsys.readouterr().out
    render.claude_status(
        ClaudeStatusData(email=None, account_number=None, tag="", total_accounts=0, usage=None)
    )
    assert "No active Claude account" in capsys.readouterr().out


def test_fetch_progress_is_noop_without_terminal() -> None:
    with render.fetch_progress("Claude") as tick:
        tick(1, 5, "me@example.com")  # must not raise or print
    # err_console is not a terminal under pytest, so nothing was rendered.


def test_sentinel_notes_are_ascii() -> None:
    for note in render.SENTINEL_NOTES.values():
        assert all(ord(ch) < 128 for ch in note)
