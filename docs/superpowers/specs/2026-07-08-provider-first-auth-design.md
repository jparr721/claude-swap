# Provider-First Auth Switching Design

## Purpose

claude-swap should move from Claude-first behavior with ad hoc Codex support to a provider-first command and data model. The new model must support explicit frontend/backend pairs, keep legacy commands as aliases where useful, and make JSON output consistent across Claude, Codex, and opencode.

The immediate supported providers are:

- `claude/default`
- `codex/openai`
- `opencode/openai`

opencode is intentionally OpenAI-only for this revision. Future contributors can add opencode backends such as Anthropic or OpenRouter by adding backend adapters and registry entries.

## Command Model

Canonical commands are provider-first:

```bash
cswap claude default list
cswap claude default status
cswap claude default add
cswap claude default switch
cswap claude default switch --to 2
cswap claude default remove 2

cswap codex openai list
cswap codex openai status
cswap codex openai add
cswap codex openai switch
cswap codex openai switch --to 2
cswap codex openai remove 2

cswap opencode openai list
cswap opencode openai status
cswap opencode openai add
cswap opencode openai switch
cswap opencode openai switch --to 2
cswap opencode openai remove 2
```

Compatibility aliases remain for existing users:

- `cswap list` -> `cswap claude default list`
- `cswap status` -> `cswap claude default status`
- `cswap add` -> `cswap claude default add`
- `cswap switch` -> `cswap claude default switch`
- `cswap switch 2` -> `cswap claude default switch --to 2`
- `cswap claude list` -> `cswap claude default list`
- `cswap codex list` -> `cswap codex openai list`
- `cswap codex switch 2` -> `cswap codex openai switch --to 2`

There is no `cswap opencode list` shorthand. opencode must always specify its backend, for example `cswap opencode openai list`.

## Architecture

The command parser should route only. Provider behavior belongs behind a registry and adapters.

Core units:

- `ProviderDefinition`: identifies one `(frontend, backend)` pair and binds adapters, display names, state names, and feature flags.
- `ProviderRegistry`: maps command paths to provider definitions and exposes aggregate providers for `cswap list`.
- `FrontendAdapter`: knows where the active auth file lives and how to read/write it.
- `BackendAdapter`: knows auth schema parsing, identity extraction, usage fetching, and token status classification.
- `ProviderAccountStore`: generic snapshot lifecycle for add, list, status, switch, remove, cache, and locking.

Initial adapters:

- `ClaudeFrontend` plus `ClaudeDefaultBackend`: wraps existing `ClaudeAccountSwitcher` behavior first, then can be migrated later.
- `CodexFrontend`: active auth at `$CODEX_HOME/auth.json`, default `~/.codex/auth.json`.
- `OpencodeFrontend`: active auth at `$OPENCODE_DATA_HOME/auth.json`, default `~/.local/share/opencode/auth.json`.
- `OpenAIBackend`: supports Codex ChatGPT auth shape and opencode OpenAI auth shape through explicit parser variants or frontend-provided schema selection.

## Provider Store Layout

New provider stores live under:

```text
<backup-root>/providers/<frontend>/<backend>/
  sequence.json
  auth/account-1.json
  auth/account-2.json
  cache/
  .lock
```

Existing Claude data can stay in the current root layout during the first migration step. Existing Codex data migrates from:

```text
<backup-root>/codex/
```

to:

```text
<backup-root>/providers/codex/openai/
```

Migration is idempotent and lock-protected. If both old and new Codex stores exist, the new store wins and the old store is left untouched.

## Switch Flow

All provider switch operations follow the same flow:

1. Resolve command path to a `ProviderDefinition`.
2. Load the provider store and lock it.
3. Read current active auth through the frontend adapter.
4. Extract current identity through the backend adapter.
5. If the current active auth belongs to a managed slot, snapshot it before switching.
6. Resolve the target by slot or label.
7. Validate the stored target auth through the backend adapter.
8. Write the target auth through the frontend adapter.
9. Persist active slot and timestamp in the provider store.

The switch only mutates the selected provider store and active auth file.

## Listing And JSON

Human `cswap list` remains aggregate and frontend-first:

```text
Claude accounts:
  <account rows>

Codex / OpenAI accounts:
  <account rows>

opencode / OpenAI accounts:
  <account rows>
```

`cswap list --json` jumps straight to schema v2 and does not preserve old top-level Claude fields:

```json
{
  "schemaVersion": 2,
  "providers": {
    "claude": {
      "default": {
        "activeAccountNumber": 3,
        "accounts": []
      }
    },
    "codex": {
      "openai": {
        "activeAccountNumber": 1,
        "accounts": []
      }
    },
    "opencode": {
      "openai": {
        "activeAccountNumber": 1,
        "accounts": []
      }
    }
  }
}
```

Aggregate JSON includes only managed non-Claude providers.

## Error Handling

Explicit provider commands fail normally. For example, `cswap opencode openai list` exits non-zero if opencode OpenAI state is corrupt.

Aggregate `cswap list` isolates non-Claude provider failures. Human output prints a warning to stderr and continues. JSON output includes provider-level errors:

```json
{
  "schemaVersion": 2,
  "providers": {
    "codex": {
      "openai": {
        "error": {
          "type": "config_error",
          "message": "state file is not valid JSON"
        }
      }
    }
  }
}
```

Token status comes from the backend adapter and is structured:

- `ok`
- `expired`
- `missing-refresh-token`
- `unsupported-auth-shape`
- `usage-unavailable`

Server rejection wins over local timestamps. If opencode's local OpenAI `expires` field is in the future but the usage endpoint returns 401 or 403, the provider reports `expired`.

## Testing

Use a shared provider behavior matrix for `codex/openai` and `opencode/openai`:

- add
- list and `ls`
- status
- switch rotation
- switch `--to`
- remove and `rm`
- JSON output
- active detection
- expired token reporting
- corrupt state handling

Provider-specific tests cover:

- Codex auth path and auth schema.
- opencode OpenAI auth path and auth schema.
- Codex store migration to `providers/codex/openai`.
- CLI aliases for existing Codex commands.
- Rejection of opencode shorthand without backend.

Claude tests cover:

- Existing command aliases route to `claude/default`.
- New canonical `cswap claude default` commands.
- `cswap list --json` emits schema v2 with `providers.claude.default`.

## Implementation Order

1. Add provider registry and provider definition types.
2. Add frontend and backend adapter boundaries.
3. Move Codex behavior behind `codex/openai` without changing behavior.
4. Add opencode OpenAI behind `opencode/openai`.
5. Wrap Claude as `claude/default`.
6. Change aggregate JSON to schema v2.
7. Add Codex store migration.
8. Update docs and command help.
