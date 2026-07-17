"""Tests for disabled self-upgrade behavior."""

from __future__ import annotations

import subprocess
import sys
import urllib.request
from unittest.mock import patch

from claude_swap.update_check import check_for_update, run_self_upgrade


def test_check_for_update_does_not_request_an_upstream_release() -> None:
    with patch.object(urllib.request, "urlopen") as mock_urlopen:
        assert check_for_update("0.0.0") is None

    mock_urlopen.assert_not_called()


def test_run_self_upgrade_does_not_invoke_a_package_manager() -> None:
    with (
        patch.object(sys, "prefix", "/tmp/uv/tools/claude-swap"),
        patch.object(subprocess, "run") as mock_run,
    ):
        assert run_self_upgrade() == 1

    mock_run.assert_not_called()
