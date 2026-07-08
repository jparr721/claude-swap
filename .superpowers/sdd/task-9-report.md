# Task 9 Report

## Summary

- Updated CLI help in `src/claude_swap/cli.py` to foreground canonical provider-first commands:
  - `cswap claude default list`
  - `cswap codex openai list`
  - `cswap opencode openai list`
  - switch examples using `--to`
- Updated `README.md` to document provider-first account switching and schema v2 `list --json` output.
- Added a help regression test in `tests/test_cli.py`.
- Kept the existing `tests/conftest.py` `OPENCODE_DATA_HOME` isolation change and included it in this task.

## RED

- Added `TestCLI.test_help_mentions_provider_first_commands` in `tests/test_cli.py`.
- Ran:

```bash
uv run pytest tests/test_cli.py::TestCLI::test_help_mentions_provider_first_commands -v
```

- Result: FAIL
- Failure evidence:
  - missing `cswap claude default list` in `python -m claude_swap --help`
  - help still showed generic `codex openai <command>` and old provider examples

## GREEN

- Updated help text and README examples.
- Re-ran:

```bash
uv run pytest tests/test_cli.py::TestCLI::test_help_mentions_provider_first_commands -v
```

- Result: PASS

## Verification

- Ran:

```bash
uv run pytest tests/test_cli.py tests/test_json_output.py -v
```

- Result: `116 passed in 2.99s`

- Ran:

```bash
uv run pytest
```

- Result: `1012 passed, 3 skipped in 20.98s`

## Files Changed

- `README.md`
- `src/claude_swap/cli.py`
- `tests/conftest.py`
- `tests/test_cli.py`

## Self-Review

- Help output now shows canonical provider-first commands for Claude, Codex, and opencode.
- README no longer documents backend-free Codex or opencode shorthand commands.
- README documents schema v2 for `list --json` and clarifies that nested provider payloads keep their provider-local schema v1 shape.
- The change is scoped to docs, help text, and the help regression test. No behavioral code paths changed.
