"""Generic provider account storage and auth switching."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_swap import oauth
from claude_swap.exceptions import AccountNotFoundError, ConfigError, ValidationError
from claude_swap.locking import FileLock
from claude_swap.models import get_timestamp
from claude_swap.json_output import SCHEMA_VERSION, usage_freshness_fields
from claude_swap.printer import accent, bolded, dimmed, format_age, muted
from claude_swap.providers.openai import OPENAI_USAGE_TIMEOUT_S
from claude_swap.providers.types import (
    AuthMetadata,
    ProviderDefinition,
    UsageFetchError,
)
from claude_swap.usage_store import FetchRecord, UsageEntry, UsageStore

_USAGE_AGE_NOTE_S = 90.0


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, text.encode("utf-8"))
        os.close(fd)
        fd = -1
        os.replace(tmp_name, str(path))
        if sys.platform != "win32":
            os.chmod(str(path), 0o600)
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise ConfigError(f"Failed to write {path}: {exc}") from exc


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2) + "\n")


def _safe_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _unix_seconds_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _clamp_percent(value: Any) -> float:
    if isinstance(value, (int, float)):
        pct = float(value)
    else:
        pct = 0.0
    return min(100.0, max(0.0, pct))


def _window_label(window: dict[str, Any]) -> str:
    seconds = window.get("limit_window_seconds")
    if not isinstance(seconds, (int, float)):
        return "?"
    hours = round(seconds / 3600)
    if hours >= 24:
        return f"{round(hours / 24)}d"
    return f"{hours}h"


def _usage_to_lines(usage: dict[str, Any]) -> list[str]:
    rows: list[tuple[str, str]] = []
    windows = usage.get("windows")
    if isinstance(windows, list):
        for window in windows:
            if not isinstance(window, dict):
                continue
            label = _safe_str(window.get("label"))
            pct = window.get("pct")
            if not label or not isinstance(pct, (int, float)):
                continue
            body = f"{pct:>3.0f}%"
            cell = oauth.fresh_reset_strings(window)
            if cell is not None:
                countdown, clock = cell
                body = f"{body}   resets {clock:<12}  in {countdown}"
            rows.append((label, body))
    plan = _safe_str(usage.get("plan"))
    if plan:
        rows.append(("Plan", plan))
    credits = usage.get("credits")
    if isinstance(credits, (int, float)):
        rows.append(("Credits", f"{credits:g}"))
    width = max((len(label) for label, _ in rows), default=0) + 1
    return [f"{label + ':':<{width}} {body}" for label, body in rows]


def _window_to_json(window: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "label": _safe_str(window.get("label")),
        "pct": window["pct"],
    }
    if "resets_at" in window:
        result["resetsAt"] = window["resets_at"]
    cell = oauth.fresh_reset_strings(window)
    if cell is not None:
        result["countdown"], result["clock"] = cell
    return result


def _usage_to_json(usage: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    windows_out: list[dict[str, Any]] = []
    windows = usage.get("windows")
    if isinstance(windows, list):
        for window in windows:
            if not isinstance(window, dict):
                continue
            pct = window.get("pct")
            if _safe_str(window.get("label")) and isinstance(pct, (int, float)):
                windows_out.append(_window_to_json(window))
    if windows_out:
        result["windows"] = windows_out
    plan = _safe_str(usage.get("plan"))
    if plan:
        result["plan"] = plan
    credits = usage.get("credits")
    if isinstance(credits, (int, float)):
        result["credits"] = credits
    return result


def _usage_fields(entry: UsageEntry) -> tuple[str, dict[str, Any] | None]:
    usage = entry.decision_value()
    if isinstance(usage, dict):
        return "ok", _usage_to_json(usage)
    return "unavailable", None


class ProviderAccountStore:
    def __init__(self, definition: ProviderDefinition) -> None:
        self.definition = definition
        self.state_dir = definition.state_dir
        self.auth_dir = self.state_dir / "auth"
        self.sequence_file = self.state_dir / "sequence.json"
        self.lock_file = self.state_dir / ".lock"
        self.auth_path = definition.frontend.active_auth_path()
        self._usage_store = UsageStore(self.state_dir / "cache")

    def _setup_directories(self) -> None:
        for directory in (self.state_dir, self.auth_dir):
            directory.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                os.chmod(directory, 0o700)

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"{self.definition.display_name} state file is not valid JSON ({path}): {exc}"
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"Failed to read {self.definition.display_name} state file {path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ConfigError(
                f"{self.definition.display_name} state file must contain a JSON object: {path}"
            )
        return data

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        _atomic_write_json(path, data)

    def _init_sequence_file(self) -> None:
        if self.sequence_file.exists():
            return
        self._write_json(
            self.sequence_file,
            {
                "activeAccountNumber": None,
                "lastUpdated": get_timestamp(),
                "sequence": [],
                "accounts": {},
            },
        )

    def _sequence_data(self) -> dict[str, Any]:
        data = self._read_json(self.sequence_file)
        if data is None:
            return {
                "activeAccountNumber": None,
                "lastUpdated": get_timestamp(),
                "sequence": [],
                "accounts": {},
            }
        sequence = data.setdefault("sequence", [])
        accounts = data.setdefault("accounts", {})
        if not isinstance(sequence, list) or not all(
            isinstance(number, int) for number in sequence
        ):
            raise ConfigError(
                f"{self.definition.display_name} state sequence must be a list of numbers: "
                f"{self.sequence_file}"
            )
        if not isinstance(accounts, dict):
            raise ConfigError(
                f"{self.definition.display_name} state accounts must be an object: "
                f"{self.sequence_file}"
            )
        if not all(isinstance(key, str) and key.isdigit() for key in accounts):
            raise ConfigError(
                f"{self.definition.display_name} state account keys must be numeric: "
                f"{self.sequence_file}"
            )
        return data

    def _auth_backup_path(self, account_num: str) -> Path:
        return self.auth_dir / f"account-{account_num}.json"

    def _read_active_auth(self) -> str | None:
        try:
            text = self.auth_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ConfigError(
                f"Failed to read {self.definition.display_name} auth file {self.auth_path}: {exc}"
            ) from exc
        if text.strip():
            return text
        return None

    def _read_required_active_auth(self) -> str:
        text = self._read_active_auth()
        if text is None:
            frontend = self.definition.ref.frontend
            command = self.definition.frontend.login_command
            raise ConfigError(
                f"No active {frontend} auth found at {self.auth_path}. "
                f"Run '{command}' first."
            )
        return text

    def _write_active_auth(self, text: str) -> None:
        _atomic_write_text(self.auth_path, text)

    def _activate_auth_symlink(self, target_file: Path) -> None:
        """Atomically point the active auth file at ``target_file`` (a symlink).

        Codex writes auth.json in place (open+truncate+write, no rename), so a
        symlink here is followed and written through to ``target_file`` - the
        per-account credential rotates in that file and is never overwritten by
        a stale copy. Replaces either a pre-existing real file or an older
        symlink atomically via ``os.replace`` of a sibling temp symlink.
        """
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.auth_path.with_name(f".{self.auth_path.name}.{os.getpid()}.tmp")
        try:
            if tmp.is_symlink() or tmp.exists():
                tmp.unlink()
            os.symlink(os.fspath(target_file), os.fspath(tmp))
            os.replace(os.fspath(tmp), os.fspath(self.auth_path))
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise ConfigError(
                f"Failed to point {self.definition.display_name} active auth at "
                f"{target_file}: {exc}"
            ) from exc

    def _active_symlink_target(self) -> Path | None:
        """Absolute target of the active auth symlink, or None if not a symlink."""
        if not self.auth_path.is_symlink():
            return None
        return Path(os.path.realpath(self.auth_path))

    def _adopt_active_real_file(self, data: dict[str, Any]) -> None:
        """Fold a pre-symlink real auth.json into its managed account's target.

        Before the first symlink switch the active auth file is a real file that
        Codex has been rotating in place - the freshest copy of whichever account
        is live. Copy it into that account's target file so repointing the
        symlink away never loses it. Refuse if it belongs to no managed account,
        rather than silently discarding a live credential.
        """
        if self.auth_path.is_symlink():
            return
        text = self._read_active_auth()
        if text is None:
            return
        num = self._current_account_number(data, text)
        if num is None:
            ref = self.definition.ref
            raise ConfigError(
                f"The active {self.definition.display_name} auth is not a managed "
                f"account. Add it first with: cswap {ref.frontend} {ref.backend} add"
            )
        self._write_account_auth(num, text)

    def _read_account_auth(self, account_num: str) -> str:
        try:
            return self._auth_backup_path(account_num).read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            ref = self.definition.ref
            raise ConfigError(
                f"{self.definition.display_name} Account-{account_num} has no stored auth. "
                f"Re-add it with: cswap {ref.frontend} {ref.backend} add --slot {account_num}"
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"Failed to read {self.definition.display_name} Account-{account_num} auth: {exc}"
            ) from exc

    def _write_account_auth(self, account_num: str, text: str) -> None:
        _atomic_write_text(self._auth_backup_path(account_num), text)

    def _metadata(self, text: str) -> AuthMetadata:
        return self.definition.backend.metadata_from_text(text)

    def _stored_metadata(self, account_num: str, text: str) -> AuthMetadata:
        try:
            return self._metadata(text)
        except ConfigError as exc:
            raise ConfigError(
                f"stored auth for {self.definition.display_name} Account-{account_num} is invalid: {exc}"
            ) from exc

    def _next_account_number(self, data: dict[str, Any]) -> int:
        accounts = data.get("accounts", {})
        return max((int(number) for number in accounts.keys()), default=0) + 1

    def _derive_label(
        self, label: str | None, metadata: AuthMetadata, account_num: str
    ) -> str:
        if label is not None:
            normalized = label.strip()
            if not normalized:
                raise ValidationError(
                    f"{self.definition.display_name} account label cannot be empty"
                )
            if normalized.isdigit():
                raise ValidationError(
                    f"{self.definition.display_name} account label cannot be only digits"
                )
            return normalized
        if metadata.account_id:
            return metadata.account_id
        return f"{self.definition.default_label_prefix}-{account_num}"

    def _account_by_label(self, data: dict[str, Any], label: str) -> str | None:
        for account_num, account in data.get("accounts", {}).items():
            if account.get("label") == label:
                return account_num
        return None

    def _current_account_number(
        self, data: dict[str, Any], active_auth: str | None
    ) -> str | None:
        if active_auth is None:
            target = self._active_symlink_target()
            if target is not None:
                for account_num in data.get("accounts", {}):
                    if self._auth_backup_path(account_num).resolve() == target:
                        return account_num
        auth_text = active_auth
        if auth_text is None:
            auth_text = self._read_active_auth()
        if auth_text is None:
            return None
        metadata = self._metadata(auth_text)
        accounts = data.get("accounts", {})
        if metadata.account_id:
            for account_num, account in accounts.items():
                if account.get("accountId") == metadata.account_id:
                    return account_num
        for account_num, account in accounts.items():
            if account.get("fingerprint") == metadata.fingerprint:
                return account_num
        return None

    def _resolve_account_identifier(
        self, data: dict[str, Any], identifier: str
    ) -> str | None:
        accounts = data.get("accounts", {})
        if identifier.isdigit():
            if identifier in accounts:
                return identifier
            return None
        return self._account_by_label(data, identifier)

    def _set_account_record(
        self, data: dict[str, Any], account_num: str, label: str, metadata: AuthMetadata
    ) -> None:
        existing = data.get("accounts", {}).get(account_num, {})
        data["accounts"][account_num] = {
            "label": label,
            "accountId": metadata.account_id,
            "authMode": metadata.auth_mode,
            "fingerprint": metadata.fingerprint,
            "added": existing.get("added") or get_timestamp(),
        }
        numeric = int(account_num)
        if numeric not in data["sequence"]:
            data["sequence"].append(numeric)
            data["sequence"].sort()

    def _account_ref(
        self, data: dict[str, Any], account_num: str | None
    ) -> dict[str, Any] | None:
        if account_num is None:
            return None
        account = data.get("accounts", {}).get(account_num)
        if not account:
            return None
        return {"number": int(account_num), "label": account.get("label", "")}

    def _usage_identities(self, data: dict[str, Any]) -> dict[str, tuple[str, str]]:
        identities: dict[str, tuple[str, str]] = {}
        for account_num, account in data.get("accounts", {}).items():
            account_id = _safe_str(account.get("accountId"))
            fingerprint = _safe_str(account.get("fingerprint"))
            label = _safe_str(account.get("label"))
            if account_id and fingerprint:
                identities[account_num] = (f"{account_id}:{fingerprint}", "")
            else:
                identities[account_num] = (account_id or fingerprint or label or account_num, "")
        return identities

    def _fetch_usage_record(self, account_num: str) -> FetchRecord:
        try:
            auth_text = self._read_account_auth(account_num)
            usage = self.definition.backend.fetch_usage(auth_text, OPENAI_USAGE_TIMEOUT_S)
        except ConfigError as exc:
            return FetchRecord(error=str(exc))
        if isinstance(usage, dict):
            usage_dict = usage
            windows = usage_dict.get("windows")
            if isinstance(windows, list):
                normalized_windows: list[dict[str, Any]] = []
                for window in windows:
                    if not isinstance(window, dict):
                        continue
                    normalized: dict[str, Any] = {
                        "label": _safe_str(window.get("label")) or _window_label(window),
                        "pct": _clamp_percent(window.get("pct", window.get("used_percent"))),
                    }
                    resets_at = window.get("resets_at")
                    if resets_at is None:
                        resets_at = _unix_seconds_to_iso(window.get("reset_at"))
                    if isinstance(resets_at, str):
                        normalized["resets_at"] = resets_at
                    normalized_windows.append(normalized)
                usage_dict = dict(usage_dict)
                usage_dict["windows"] = normalized_windows
            return FetchRecord(usage=usage_dict)
        if isinstance(usage, UsageFetchError):
            return FetchRecord(error=usage.message, retry_after_s=usage.retry_after_s)
        return FetchRecord(error=usage or "usage unavailable")

    def _collect_usage_entries(self, data: dict[str, Any]) -> dict[str, UsageEntry]:
        identities = self._usage_identities(data)
        if not identities:
            return {}
        store = self._usage_store
        now = store.clock()
        entries = store.entries(identities)
        to_fetch = [
            account_num
            for account_num in sorted(identities.keys(), key=int)
            if not entries[account_num].fresh(now)
            and not entries[account_num].in_backoff(now)
            and not entries[account_num].claimed(now)
        ]
        if to_fetch:
            store.claim(to_fetch, identities)
            store.record(
                {
                    account_num: self._fetch_usage_record(account_num)
                    for account_num in to_fetch
                },
                identities,
            )
            entries = store.entries(identities)
        return entries

    def _provider_payload(self) -> dict[str, str]:
        return {
            "frontend": self.definition.ref.frontend,
            "backend": self.definition.ref.backend,
        }

    def _build_list_payload(
        self, data: dict[str, Any], entries: dict[str, UsageEntry]
    ) -> dict[str, Any]:
        active_num = self._current_account_number(data, None)
        accounts: list[dict[str, Any]] = []
        for account_num in sorted(data.get("accounts", {}).keys(), key=int):
            account = data["accounts"][account_num]
            entry = entries.get(account_num, UsageEntry())
            usage_status, usage = _usage_fields(entry)
            row: dict[str, Any] = {
                "number": int(account_num),
                "label": account.get("label", ""),
                "active": account_num == active_num,
                "usageStatus": usage_status,
                "usage": usage,
            }
            if usage is not None:
                row.update(usage_freshness_fields(entry.fetched_at, entry.age_s))
            elif entry.last_error:
                row["usageError"] = entry.last_error
            accounts.append(row)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "provider": self._provider_payload(),
            "activeAccountNumber": int(active_num) if active_num is not None else None,
            "accounts": accounts,
        }

    def _usage_lines(self, entry: UsageEntry) -> list[str]:
        if entry.last_good is not None:
            lines = _usage_to_lines(entry.last_good)
            if (
                lines
                and entry.age_s is not None
                and entry.age_s > _USAGE_AGE_NOTE_S
                and entry.fetched_at is not None
            ):
                lines[-1] += f" - {format_age(int(entry.fetched_at * 1000))}"
            if lines:
                return [muted(line) for line in lines]
        detail = "usage unavailable"
        if entry.last_error:
            detail += f" ({entry.last_error})"
        return [dimmed(detail)]

    def add_account(self, label: str | None, slot: int | None) -> None:
        active_auth = self._read_required_active_auth()
        metadata = self._metadata(active_auth)
        self._setup_directories()
        self._init_sequence_file()

        with FileLock(self.lock_file):
            data = self._sequence_data()
            existing_num = self._current_account_number(data, active_auth)
            if slot is None:
                account_num = existing_num or str(self._next_account_number(data))
            else:
                if slot < 1:
                    raise ConfigError(
                        f"{self.definition.display_name} slot number must be >= 1"
                    )
                account_num = str(slot)
                if existing_num is not None and existing_num != account_num:
                    raise ValidationError(
                        f"This {self.definition.display_name} auth is already stored as "
                        f"{self.definition.display_name} Account-{existing_num}"
                    )
                if (
                    account_num in data.get("accounts", {})
                    and existing_num != account_num
                ):
                    raise ConfigError(
                        f"{self.definition.display_name} Account-{account_num} already exists. "
                        f"Remove it first or choose another slot."
                    )

            existing_account = data.get("accounts", {}).get(account_num, {})
            if label is None and existing_account:
                resolved_label = _safe_str(existing_account.get("label"))
            else:
                resolved_label = self._derive_label(label, metadata, account_num)
            duplicate = self._account_by_label(data, resolved_label)
            if duplicate is not None and duplicate != account_num:
                raise ValidationError(
                    f"{self.definition.display_name} account label '{resolved_label}' already "
                    f"exists as Account-{duplicate}"
                )

            self._write_account_auth(account_num, active_auth)
            self._set_account_record(data, account_num, resolved_label, metadata)
            data["activeAccountNumber"] = int(account_num)
            data["lastUpdated"] = get_timestamp()
            self._write_json(self.sequence_file, data)

        action = "Updated" if existing_num == account_num else "Added"
        print(
            f"{accent(action)} {self.definition.display_name} Account-{account_num}: "
            f"{resolved_label}"
        )

    def list_accounts(self, json_output: bool) -> dict | None:
        data = self._sequence_data()
        entries = self._collect_usage_entries(data)
        if json_output:
            return self._build_list_payload(data, entries)

        accounts = data.get("accounts", {})
        if not accounts:
            print(dimmed(f"No {self.definition.display_name} accounts are managed yet."))
            return None

        payload = self._build_list_payload(data, entries)
        print(bolded(f"{self.definition.display_name} accounts:"))
        for account in payload["accounts"]:
            marker = ""
            if account["active"]:
                marker = f" {accent('(active)')}"
            print(f"  {account['number']}: {account['label']}{marker}")
            entry = entries.get(str(account["number"]), UsageEntry())
            for line in self._usage_lines(entry):
                print(f"     {line}")
        return None

    def status(self, json_output: bool) -> dict | None:
        data = self._sequence_data()
        active_auth = self._read_active_auth()
        if active_auth is None:
            payload: dict[str, Any] = {
                "schemaVersion": SCHEMA_VERSION,
                "provider": self._provider_payload(),
                "active": None,
            }
        else:
            current_num = self._current_account_number(data, active_auth)
            if current_num is None:
                payload = {
                    "schemaVersion": SCHEMA_VERSION,
                    "provider": self._provider_payload(),
                    "active": {"managed": False},
                }
            else:
                account = data["accounts"][current_num]
                payload = {
                    "schemaVersion": SCHEMA_VERSION,
                    "provider": self._provider_payload(),
                    "active": {
                        "number": int(current_num),
                        "label": account.get("label", ""),
                        "managed": True,
                    },
                    "totalManagedAccounts": len(data.get("accounts", {})),
                }
        if json_output:
            return payload

        active = payload["active"]
        if active is None:
            print(
                f"{bolded(f'{self.definition.display_name} status:')} "
                f"{dimmed(f'No active {self.definition.display_name} auth')}"
            )
        elif active.get("managed"):
            active_label = accent(f"Account-{active['number']}")
            print(
                f"{bolded(f'{self.definition.display_name} status:')} "
                f"{active_label} ({active['label']})"
            )
        else:
            print(
                f"{bolded(f'{self.definition.display_name} status:')} {muted('(not managed)')}"
            )
        return None

    def _rotation_target(self, data: dict[str, Any]) -> str | None:
        sequence = data.get("sequence", [])
        if not sequence:
            return None
        if len(sequence) == 1:
            return str(sequence[0])
        current_num = self._current_account_number(data, None)
        if current_num is None:
            active = data.get("activeAccountNumber")
            if active is not None:
                current_num = str(active)
            else:
                current_num = str(sequence[0])
        try:
            current_index = sequence.index(int(current_num))
        except ValueError:
            current_index = 0
        return str(sequence[(current_index + 1) % len(sequence)])

    def switch(self, identifier: str | None, json_output: bool) -> dict | None:
        self._setup_directories()
        if self.definition.switch_mode == "snapshot-refused":
            raise ConfigError(
                f"{self.definition.display_name} cannot safely restore stored OpenAI OAuth "
                f"snapshots. Run '{self.definition.frontend.login_command}' for the target "
                "account, then re-add it."
            )
        return self._switch_symlink(identifier, json_output)

    def _switch_symlink(self, identifier: str | None, json_output: bool) -> dict | None:
        """Switch by repointing the active auth symlink at the target's stored file.

        No byte copy and no re-snapshot: the outgoing account's file is the live
        file Codex already rotates in place, so it is current by construction.
        """
        with FileLock(self.lock_file):
            data = self._sequence_data()
            if not data.get("accounts"):
                raise ConfigError(
                    f"No {self.definition.display_name} accounts are managed yet"
                )

            if identifier is None:
                target_account = self._rotation_target(data)
            else:
                target_account = self._resolve_account_identifier(data, identifier)
            if target_account is None or target_account not in data.get("accounts", {}):
                raise AccountNotFoundError(
                    f"No {self.definition.display_name} account found with identifier: {identifier}"
                )

            target_file = self._auth_backup_path(target_account)
            if not target_file.exists():
                ref = self.definition.ref
                raise ConfigError(
                    f"{self.definition.display_name} Account-{target_account} has no stored "
                    f"credential. Re-add it with: cswap {ref.frontend} {ref.backend} "
                    f"add --slot {target_account}"
                )

            current_num = self._current_account_number(data, None)
            from_ref = self._account_ref(data, current_num)
            to_ref = self._account_ref(data, target_account)
            if current_num == target_account and self.auth_path.is_symlink():
                result = {
                    "schemaVersion": SCHEMA_VERSION,
                    "provider": self._provider_payload(),
                    "switched": False,
                    "from": from_ref,
                    "to": to_ref,
                    "reason": "already-active",
                    "message": (
                        f"Already on {self.definition.display_name} Account-{target_account}"
                    ),
                }
                if json_output:
                    return result
                print(
                    f"{accent('Already on')} {self.definition.display_name} "
                    f"Account-{target_account}"
                )
                return None

            self._adopt_active_real_file(data)
            self._activate_auth_symlink(target_file)
            data["activeAccountNumber"] = int(target_account)
            data["lastUpdated"] = get_timestamp()
            self._write_json(self.sequence_file, data)

        label = data["accounts"][target_account].get("label", "")
        result = {
            "schemaVersion": SCHEMA_VERSION,
            "provider": self._provider_payload(),
            "switched": True,
            "from": from_ref,
            "to": to_ref,
            "reason": "switched",
            "message": (
                f"Switched {self.definition.display_name} to Account-{target_account} ({label})"
            ),
        }
        if json_output:
            return result
        print(
            f"{accent(f'Switched {self.definition.display_name} to')} "
            f"Account-{target_account} ({label})"
        )
        return None

    def remove_account(self, identifier: str) -> None:
        self._setup_directories()
        with FileLock(self.lock_file):
            data = self._sequence_data()
            account_num = self._resolve_account_identifier(data, identifier)
            if account_num is None:
                raise AccountNotFoundError(
                    f"No {self.definition.display_name} account found with identifier: {identifier}"
                )
            account = data.get("accounts", {}).get(account_num)
            if account is None:
                raise AccountNotFoundError(
                    f"{self.definition.display_name} Account-{account_num} does not exist"
                )

            self._auth_backup_path(account_num).unlink(missing_ok=True)
            data["accounts"].pop(account_num, None)
            numeric = int(account_num)
            if numeric in data.get("sequence", []):
                data["sequence"].remove(numeric)
            if data.get("activeAccountNumber") == numeric:
                data["activeAccountNumber"] = None
            data["lastUpdated"] = get_timestamp()
            self._write_json(self.sequence_file, data)

        print(
            f"{accent('Removed')} {self.definition.display_name} Account-{account_num}: "
            f"{account.get('label', '')}"
        )
