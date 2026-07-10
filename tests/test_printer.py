"""Tests for the printer module."""

from __future__ import annotations

import sys
from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from claude_swap import printer


def test_theme_and_consoles_exist() -> None:
    assert printer.console.stderr is False
    assert printer.err_console.stderr is True
    for style in ("accent", "muted", "dim", "warn", "err"):
        assert printer.THEME.styles[style] is not None


def test_helpers_return_plain_text_without_terminal() -> None:
    # Under pytest stdout is not a terminal: helpers must return the bare text.
    assert printer.accent("hello") == "hello"
    assert printer.dimmed("hello") == "hello"
    assert printer.bolded("hello") == "hello"


def test_helpers_emit_ansi_when_forced() -> None:
    # Monkeypatching printer.console's cached _color_system to trigger
    # re-detection proved unstable across rich versions (setting it to
    # None short-circuits color_system to None rather than re-detecting).
    # Instead, construct a Console the same way with force_terminal=True
    # at construction time (the supported way to force color) and verify
    # the same style machinery emits escape codes there.
    forced_console = Console(theme=printer.THEME, force_terminal=True)
    with forced_console.capture() as capture:
        forced_console.print(Text("hello", style="accent"), end="", soft_wrap=True)
    styled = capture.get()
    assert "hello" in styled
    assert styled != "hello"  # carries escape codes


def test_helpers_never_wrap_long_text() -> None:
    long = "x" * 500
    assert printer.muted(long).replace("\x1b", "").count("\n") == 0


def test_error_prints_to_stderr(capsys) -> None:
    printer.error("boom")
    captured = capsys.readouterr()
    assert "boom" in captured.err
    assert captured.out == ""


def test_warning_prints_to_stdout(capsys) -> None:
    printer.warning("careful")
    assert "careful" in capsys.readouterr().out


class TestForceUtf8Output:
    """Tests for force_utf8_output (issue #113: cp1252 console crash)."""

    def test_reconfigures_legacy_stream_to_utf8(self, monkeypatch):
        # A cp1252-encoded stdout raises on the tool's glyphs before the fix.
        import io

        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with pytest.raises(UnicodeEncodeError):
            stream.write("● → ├ ─ └")
            stream.flush()

        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", stream)
        printer.force_utf8_output()

        assert stream.encoding == "utf-8"
        # No longer raises now that the stream encodes UTF-8.
        stream.write("● → ├ ─ └")
        stream.flush()

    def test_no_op_on_streams_without_reconfigure(self, monkeypatch):
        # StringIO has no reconfigure(); the guard must skip it silently.
        monkeypatch.setattr(sys, "stdout", StringIO())
        monkeypatch.setattr(sys, "stderr", StringIO())
        printer.force_utf8_output()  # must not raise
