# Task 8 Report

## Summary
- Added canonical `cswap claude default <command>` routing in `src/claude_swap/cli.py`.
- Added top-level `--to` normalization so `claude default switch --to 2` reaches the existing `switch_to` path.
- Kept the existing top-level Claude commands unchanged.
- Added CLI tests for the canonical Claude routes in `tests/test_cli.py`.

## RED Evidence
- Command:
  - `uv run pytest tests/test_cli.py::TestSubcommandAliases::test_claude_default_list_dispatches_to_claude_switcher tests/test_cli.py::TestSubcommandAliases::test_claude_default_switch_to_dispatches_to_claude_switcher -v`
- Failure before the fix:
  - `claude-swap: error: unrecognized arguments: claude default list`
  - `claude-swap: error: unrecognized arguments: claude default switch 2`

## GREEN Evidence
- Command:
  - `uv run pytest tests/test_cli.py::TestSubcommandAliases::test_claude_default_list_dispatches_to_claude_switcher tests/test_cli.py::TestSubcommandAliases::test_claude_default_switch_to_dispatches_to_claude_switcher -v`
- Result:
  - 2 passed
- Command:
  - `uv run pytest tests/test_cli.py -v`
- Result:
  - 87 passed

## Files Changed
- `src/claude_swap/cli.py`
- `tests/test_cli.py`

## Self-Review
- Routing is limited to `claude default` and does not add broader provider aliases.
- Existing top-level `list`, `switch`, and related commands still use the same parser path.
- `--to` is normalized into the existing `switch_to` flow instead of adding a separate code path.
- No registry change was required for the behavior covered by this task.

## Fix Follow-Up
- Command:
  - `uv run pytest tests/test_cli.py::TestSubcommandAliases::test_claude_default_list_dispatches_to_claude_switcher tests/test_cli.py::TestSubcommandAliases::test_claude_default_switch_to_dispatches_to_claude_switcher -v`
  - `uv run pytest tests/test_cli.py -v`
- Result:
  - 2 passed
  - 88 passed
- Change:
  - Removed the global top-level `--to` alias from `src/claude_swap/cli.py`.
  - Added claude-default-only translation to `--switch-to` before the main parser.
  - Added a regression test that `cswap switch --to 2` is rejected.
