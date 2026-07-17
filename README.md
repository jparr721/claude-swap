# claude-swap

Multi-account switcher for Claude Code. Easily switch between multiple Claude accounts without logging out, or let it switch for you before you hit a rate limit. Track usage for every account at a glance, and run accounts in parallel. Works with both the Claude Code CLI and the VS Code extension.

**Command format:** `cswap <frontend> <verb>`. Backends are fixed: Claude Code uses Anthropic and Codex uses OpenAI.

## Installation

### Using uv (recommended)

```bash
uv tool install claude-swap
```

### Using pipx

```bash
pipx install claude-swap
```

### From source

```bash
git clone https://github.com/jparr721/claude-swap.git
cd claude-swap
uv sync
uv run cswap --help
```

### Updating

`cswap upgrade` is disabled for this distribution. Update it from the source you installed from.

## Usage

### Add your first account

Log into Claude Code with your first account, then:

```bash
cswap claude add
```

### Add more accounts

Log in with another account, then:

```bash
cswap claude add
```

### Switch accounts

Rotate to the next account:

```bash
cswap claude switch
```

Or switch to a specific account:

```bash
cswap claude switch 2
cswap claude switch user@example.com
```

Not sure which one? `cswap ls` is the dashboard - every account's 5-hour and 7-day usage and reset times at a glance:

```bash
cswap ls
```

Or let claude-swap auto-pick by remaining quota - `cswap claude switch --strategy best` (most quota left) or `--strategy next-available` (skip rate-limited accounts).

**Note:** You usually don't need to restart — on Linux/Windows the new account is picked up automatically, and on macOS after the Keychain cache expires. To apply it instantly, restart Claude Code or reopen the VS Code extension tab. See [Tips](#tips) for the per-platform details.

### Account management

Claude and Codex have fixed backends:

```bash
cswap claude list
cswap codex list
cswap claude switch --to 2
```

Every account command follows `cswap <frontend> <verb>`.

Codex support is separate from Claude account switching. `cswap` adds a Codex account by driving Codex's own device-auth login, so you never have to run `codex login` by hand:

```bash
cswap codex add --label work        # runs `codex login --device-auth`, then registers it
cswap codex add --label personal     # add a second account the same way
```

Switch between them instantly and non-destructively:

```bash
cswap codex switch --to personal     # or: --to <number>; no argument rotates
cswap codex list
cswap codex status
cswap codex remove work
cswap codex list --json
```

Switching only repoints the `$CODEX_HOME/auth.json` (default `~/.codex/auth.json`) symlink at the chosen account's stored credential file; it runs no login, no logout, and no token revoke, and copies no bytes. Each account's credential lives in its own file that Codex rotates in place, so switching never replays a spent refresh token. Everything else under `~/.codex` (config, sessions, skills) stays shared - only `auth.json` is per-account.

One thing no tool can change: chatgpt.com is a single session per browser profile, so the first `codex login` for an account signs that browser into that account. Day-to-day CLI switching after that never touches the browser; use separate browser profiles if you need both accounts signed in at once.

Inactive Codex accounts keep live usage: when an inactive account's access
token expires, claude-swap refreshes it against the OpenAI token endpoint
(the same grant the Codex CLI uses) and stores the rotated token in that
account's slot. The active account is never touched - the Codex CLI owns and
refreshes it in place. An account whose refresh token has been revoked shows
"re-login needed"; re-add it with `cswap codex add`.

`cswap ls` shows separate Claude and Codex account sections when they have managed accounts.

### Automatic switching

Let claude-swap watch your usage and switch for you. When the active account's 5-hour or 7-day window reaches the threshold (default 90%), it switches to the account with the most quota left - before you hit the limit, and safe to run while Claude Code is working:

```bash
cswap claude auto                     # foreground loop, polls every 60s
cswap claude auto --threshold 80      # switch earlier
cswap claude auto --once              # single check-and-switch, for cron/scripts
cswap claude auto --dry-run           # log what it would do, never switch
```

<details>
<summary>How it behaves & advanced usage</summary>

- Runs safely alongside Claude Code: switches take the same credential locks Claude Code uses, so a swap never collides with a token refresh.
- A cooldown (default 5 min) and a hysteresis margin stop it flip-flopping near the threshold; when every account is exhausted it sleeps until the earliest reset.
- Usage polling is adaptive — a couple of accounts per check, busy alternates watched more closely, exhausted ones left alone until they reset — so API traffic stays flat no matter how many accounts you manage.
- It fails safe: if a usage check errors it keeps trusting the last-known numbers while retries back off, and an expired token on an idle machine makes it hold rather than fail over (Claude Code refreshes the token on your next message).
- An account whose refresh token has died is quarantined and reported until you log in with it and re-run `cswap claude add --slot N`. API-key accounts are never rotated onto unless you pass `--include-api-key-accounts`.

For cron/systemd timers, `--once` reports the outcome in its exit code (`0` switched, `1` error, `2` nothing to do, `3` blocked - no viable target), and `--json` emits one JSON event per line:

```bash
*/5 * * * * cswap claude auto --once --json >> ~/.cswap-auto.log 2>&1
```

Defaults like the threshold and cooldown are configurable with `cswap config set autoswitch.threshold 80` — flags override them (see [Configuration](#configuration)).

</details>

### Run multiple accounts at the same time (session mode)

Launch Claude Code as a specific account in the current terminal only - every other terminal and the VS Code extension stay on your default account, so two accounts can work in parallel.

```bash
cswap claude run 2                     # launch Claude Code as account 2, here only
cswap claude run user@example.com      # by email
cswap claude run 2 -- --resume         # everything after '--' is forwarded to claude
cswap claude run 2 --share-history     # share your chat history with this account too
```

Sessions use your normal `~/.claude` setup (settings, CLAUDE.md, skills, etc.), but each account keeps its own chat history. Pass `--share-history` if you want your accounts to continue the same conversations - a session started under one account shows up in `--resume` under the others, and nothing already saved is lost. Not supported on Windows yet.

### Refresh expired tokens

If an account's token expires, log back into Claude Code with that account and re-run:

```bash
cswap claude add
```

This will update the stored credentials without creating a duplicate.

### Other commands

```bash
cswap ls                                  # overview across all providers
cswap config [list|get|set|unset|path]    # settings
cswap purge                               # remove all claude-swap data

cswap claude list|status|add|add-token|switch|remove|run|auto|export|import
cswap codex list|status|add|switch|remove
```

## Tips

- **Do you need to restart after switching?** Usually not. On **Linux and Windows**, credentials are stored in a file and Claude Code re-reads them whenever that file changes, so the new account takes effect on your next message — no restart needed. On **macOS**, credentials live in the Keychain, which Claude Code caches for about 30 seconds; a running session picks up the switch once that cache expires. Restart Claude Code (or close and reopen the VS Code extension tab) only if you want the change to apply instantly.
- **Continuing sessions after switching:** You can keep using the same Claude Code session after switching - run `cswap claude switch` in any terminal and carry on. If you'd prefer a clean start, close and reopen Claude Code (or the VS Code extension tab) and use `--resume` to pick your previous session. Either way, the first message on the new account may use extra usage as its conversation cache rebuilds.

## How it works

- Backs up OAuth tokens and config when you add an account
- Swaps credentials when you switch accounts
- Account credentials stored securely using platform-appropriate methods
- Switches (manual and automatic) hold Claude Code's own credential locks while writing, so a swap never interleaves with a token refresh
- Auto-switch freshens a target's token before activating it, and quarantines accounts whose refresh token has died (recover with `cswap claude add --slot N`)

## Data locations

| Platform | Credentials | Config backups |
|----------|-------------|----------------|
| Windows | File-based (inside the backup directory, under `credentials/`) | `~/.claude-swap-backup/` |
| macOS | macOS Keychain | `~/.claude-swap-backup/` |
| Linux / WSL | File-based (inside the backup directory, under `credentials/`) | `${XDG_DATA_HOME:-~/.local/share}/claude-swap/` |

Session-mode profiles (`cswap claude run`) live under the backup directory in `sessions/`. Tool preferences (`settings.json`) and auto-switch state (`autoswitch_state.json` - cooldown and quarantined accounts; delete it to reset) live in the backup directory root.

On Linux/WSL, set `XDG_DATA_HOME` to override the default location.

## Advanced

### Configuration

Tool preferences live in `settings.json` in the backup root; `cswap config` reads and edits it with validation, so you never have to find the file or guess valid ranges.

<details>
<summary>Commands & usage</summary>

```bash
cswap config                              # list effective settings ("(default)" = not set)
cswap config get autoswitch.threshold
cswap config set autoswitch.threshold 80  # validated: rejects out-of-range values loudly
cswap config unset autoswitch.threshold   # back to the default
cswap config path                         # where settings.json lives
```

`cswap config list` shows every key with its current value ("(default)" marks ones you haven't set); setting an invalid key or an out-of-range value fails loudly, naming the allowed range. Hand-editing the file still works - `cswap config` is just a safer front door. `list` and `get` take `--json` for scripting.

</details>

### Backup and migration

Move account data between machines or back it up:

```bash
cswap claude export backup.cswap                    # All accounts to a file
cswap claude export backup.cswap --account 2        # One account
cswap claude export backup.cswap --full             # Include full local ~/.claude.json (same-PC backup)
cswap claude import backup.cswap                    # Skips accounts that already exist
cswap claude import backup.cswap --force             # Overwrite existing
```

The export file is plaintext JSON. If you need encryption, pipe through your tool of choice (e.g. `cswap claude export - | gpg -c > backup.gpg`).

If an imported account is the one you're currently logged in as, activate the imported credentials with `cswap claude switch N --force` (a plain `switch` to the current account is a safe no-op and won't touch the import).

### JSON output for scripting

Add `--json` to `ls`, `claude list|status|switch`, or `codex list|status|switch` to emit a machine-readable JSON object on stdout (human-readable notices go to stderr). Useful for scripting auto-swap and quota tracking.

```bash
cswap ls --json                                       # all providers, schema-v2 envelope
cswap claude list --json                      # Claude accounts, schema-v1 payload
cswap codex list --json                        # Codex accounts, schema-v1 payload
cswap claude status --json                    # current active Claude account
cswap claude switch --strategy best --json    # switch, then report the result
cswap claude switch 2 --json
```

<details>
<summary>Example output & schema notes</summary>

```json
{
  "schemaVersion": 2,
  "providers": {
    "claude": {
      "default": {
        "schemaVersion": 1,
        "activeAccountNumber": 2,
        "accounts": [
          {
            "number": 2,
            "email": "you@example.com",
            "active": true,
            "usageStatus": "ok",
            "usage": {
              "fiveHour": {
                "pct": 25.0,
                "resetsAt": "2026-06-22T23:29:59Z"
              },
              "sevenDay": {
                "pct": 16.0,
                "resetsAt": "2026-06-26T17:59:59Z"
              }
            }
          }
        ]
      }
    }
  }
}
```

`cswap ls --json` returns a schema v2 provider envelope. Each provider entry contains its existing schema v1 payload. `cswap claude status --json` and `cswap claude switch --json` still return their schema v1 payloads directly. On a handled error stdout is `{"schemaVersion":1,"error":{"type":"ConfigError","message":"invalid config"}}` with a non-zero exit code. `switch` (bare, by target, or `--to`) reports `{"switched": true|false, "from": {"number": 1}, "to": {"number": 2}, "reason": "requested"}`.

Usage is served from a per-account cache: when the usage API is briefly unreachable, the last-known numbers are shown instead of nothing (the human view marks them with their age as a standalone dim line under the usage bars, e.g. `2m ago`). Rows with usage carry additive `usageFetchedAt`/`usageAgeSeconds` fields telling you how old the measurement is.

</details>

`cswap claude auto --json` emits an event *stream* instead — one JSON object per line (`{"schemaVersion":1,"event":"switch","ts":…, …}` with kinds like `poll`, `switch`, `no-switch`, `account-quarantined`, `all-exhausted`, `error`). The contract is additive: new kinds and fields may appear, so scripts should ignore unknown ones.

### Add an account from a raw token or API key

If you only have a long-lived setup-token (e.g., produced by `claude setup-token`)
or a managed API key (`sk-ant-api...`) and you don't want to log in via the browser
flow first - useful on headless servers or when receiving a token from another
machine - register it directly. The token type is auto-detected:

```bash
cswap claude add-token sk-ant-oat01-...             # OAuth setup-token
cswap claude add-token sk-ant-api03-...             # managed API key
cswap claude add-token sk-ant-oat01-... --slot 3
cswap claude add-token - --slot 3                   # read token from stdin
cswap claude add-token --email user@example.com     # optional label override
```

`--email` is optional; omitted values use `setup-token-{slot}@token.local`
(or `api-key-{slot}@token.local` for API keys). No Anthropic API calls are made.

**API-key accounts.** An `sk-ant-api...` value registers a managed API-key account
(the kind Claude Code uses after `/login` with a key) rather than an OAuth
setup-token. It switches like any other account; since API keys have no subscription
quota, they show no usage and the usage-aware `switch` strategies never skip them as
rate-limited.

## Uninstall

Remove all data:

```bash
cswap purge
```

Then uninstall the tool:

```bash
uv tool uninstall claude-swap
# or
pipx uninstall claude-swap
```

## Requirements

- Python 3.12+
- Claude Code installed and logged in

## License

MIT
