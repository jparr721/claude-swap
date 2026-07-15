"""Tests for `cswap swap` (ClaudeAccountSwitcher.swap_accounts)."""

from pathlib import Path

import pytest

from claude_swap.exceptions import (
    AccountNotFoundError,
    ValidationError,
)
from claude_swap.switcher import ClaudeAccountSwitcher


class TestSwapAccounts:
    """Test ClaudeAccountSwitcher.swap_accounts()."""

    def _write(self, switcher, data):
        switcher._setup_directories()
        switcher._write_json(switcher.sequence_file, data)

    def test_swap_by_number(self, temp_home: Path, sample_sequence_data: dict):
        switcher = ClaudeAccountSwitcher()
        self._write(switcher, sample_sequence_data)

        num_a, num_b = switcher.swap_accounts("1", "2")

        assert (num_a, num_b) == ("1", "2")
        data = switcher._get_sequence_data()
        assert data["accounts"]["1"]["email"] == "account2@example.com"
        assert data["accounts"]["2"]["email"] == "account1@example.com"

    def test_swap_moves_active_number_with_account(
        self, temp_home: Path, sample_sequence_data: dict
    ):
        switcher = ClaudeAccountSwitcher()
        self._write(switcher, sample_sequence_data)
        assert sample_sequence_data["activeAccountNumber"] == 1

        switcher.swap_accounts("1", "2")

        data = switcher._get_sequence_data()
        # account1 was active and now lives in slot 2
        assert data["activeAccountNumber"] == 2
        assert data["accounts"]["2"]["email"] == "account1@example.com"

    def test_swap_updates_rotation_sequence(
        self, temp_home: Path, sample_sequence_data: dict
    ):
        switcher = ClaudeAccountSwitcher()
        self._write(switcher, sample_sequence_data)

        switcher.swap_accounts("1", "2")

        data = switcher._get_sequence_data()
        assert data["sequence"] == [2, 1]

    def test_swap_by_email_and_alias(
        self, temp_home: Path, sample_sequence_data: dict
    ):
        switcher = ClaudeAccountSwitcher()
        sample_sequence_data["accounts"]["2"]["alias"] = "dev"
        self._write(switcher, sample_sequence_data)

        num_a, num_b = switcher.swap_accounts("account1@example.com", "dev")

        assert (num_a, num_b) == ("1", "2")
        data = switcher._get_sequence_data()
        # The alias travels with its account into the new slot.
        assert data["accounts"]["1"].get("alias") == "dev"
        assert data["accounts"]["2"].get("alias") is None

    def test_swap_moves_credential_and_config_backups(
        self, temp_home: Path, sample_sequence_data: dict
    ):
        switcher = ClaudeAccountSwitcher()
        self._write(switcher, sample_sequence_data)
        switcher._write_account_credentials("1", "account1@example.com", "creds-one")
        switcher._write_account_config("1", "account1@example.com", "config-one")
        switcher._write_account_credentials("2", "account2@example.com", "creds-two")
        switcher._write_account_config("2", "account2@example.com", "config-two")

        switcher.swap_accounts("1", "2")

        assert (
            switcher._read_account_credentials("2", "account1@example.com")
            == "creds-one"
        )
        assert (
            switcher._read_account_config("2", "account1@example.com") == "config-one"
        )
        assert (
            switcher._read_account_credentials("1", "account2@example.com")
            == "creds-two"
        )
        assert (
            switcher._read_account_config("1", "account2@example.com") == "config-two"
        )
        # Old keys are gone.
        assert switcher._read_account_credentials("1", "account1@example.com") == ""
        assert switcher._read_account_credentials("2", "account2@example.com") == ""

    def test_swap_with_one_slot_missing_backups(
        self, temp_home: Path, sample_sequence_data: dict
    ):
        """A never-backed-up slot swaps cleanly and stays credential-less."""
        switcher = ClaudeAccountSwitcher()
        self._write(switcher, sample_sequence_data)
        switcher._write_account_credentials("1", "account1@example.com", "creds-one")

        switcher.swap_accounts("1", "2")

        assert (
            switcher._read_account_credentials("2", "account1@example.com")
            == "creds-one"
        )
        assert switcher._read_account_credentials("1", "account2@example.com") == ""

    def test_swap_same_account_rejected(
        self, temp_home: Path, sample_sequence_data: dict
    ):
        switcher = ClaudeAccountSwitcher()
        self._write(switcher, sample_sequence_data)

        with pytest.raises(ValidationError):
            switcher.swap_accounts("1", "1")

    def test_swap_unknown_identifier_rejected(
        self, temp_home: Path, sample_sequence_data: dict
    ):
        switcher = ClaudeAccountSwitcher()
        self._write(switcher, sample_sequence_data)

        with pytest.raises(AccountNotFoundError):
            switcher.swap_accounts("1", "nosuch@example.com")

    def test_swap_moves_session_profiles(
        self, temp_home: Path, sample_sequence_data: dict
    ):
        switcher = ClaudeAccountSwitcher()
        self._write(switcher, sample_sequence_data)
        session_a = switcher._session_dir("1", "account1@example.com")
        session_a.mkdir(parents=True)
        (session_a / "marker.txt").write_text("history-of-account-one")

        switcher.swap_accounts("1", "2")

        moved = switcher._session_dir("2", "account1@example.com")
        assert (moved / "marker.txt").read_text() == "history-of-account-one"
        assert not session_a.exists()
