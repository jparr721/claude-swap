# Task 4 Report - Provider Registry And Codex Compatibility Wrapper

## Status

- Complete

## RED

- Command:
  - `uv run pytest tests/test_provider_registry.py tests/test_codex_switcher.py -v`
- Result:
  - Failed during collection with `ModuleNotFoundError: No module named 'claude_swap.providers.registry'`

## GREEN

- Command:
  - `uv run pytest tests/test_provider_registry.py tests/test_codex_switcher.py -v`
- Result:
  - `10 passed`

- Command:
  - `uv run pytest tests/test_provider_store.py -v`
- Result:
  - `3 passed`

- Command:
  - `uv run pytest tests/test_openai_provider.py -v`
- Result:
  - `5 passed`

## Files Changed

- `src/claude_swap/providers/registry.py`
- `src/claude_swap/codex.py`
- `tests/test_provider_registry.py`
- `tests/test_codex_switcher.py`

## What Changed

- Added provider registry definitions for:
  - `codex/openai`
  - `opencode/openai`
- Added registry accessors:
  - `provider_definitions()`
  - `get_provider(frontend, backend)`
  - `managed_aggregate_providers()`
- Replaced legacy `src/claude_swap/codex.py` implementation with thin compatibility exports over `ProviderAccountStore`
- Preserved compatibility names used by callers and tests:
  - `CodexAccountSwitcher`
  - `OpencodeAccountSwitcher`
  - `fetch_codex_usage`
  - `fetch_opencode_usage`
  - `CODEX_USAGE_URL`
  - `CODEX_USAGE_TIMEOUT_S`
  - `_UsageFetchError`
- Routed wrapper store usage fetches back through the compatibility functions so existing monkeypatch-based tests still exercise the compatibility layer

## Self-Review

- No legacy aliases added
- No store migration logic added
- No duplicated store implementation left in `codex.py`
- Wrapper behavior is intentionally thin and provider-backed
- Focused tests cover:
  - registry contents
  - registry lookup failure
  - compatibility usage fetch behavior
  - codex wrapper usage hook integration
  - opencode wrapper auth switching through provider store

## Commit

- `Register provider-backed Codex and opencode switchers`
