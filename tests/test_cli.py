import logging

from key2ser.cli import _resolve_log_level, _unsupported_platform_message


def test_unsupported_platform_message_for_windows() -> None:
    assert _unsupported_platform_message("win32") is not None


def test_unsupported_platform_message_for_linux() -> None:
    assert _unsupported_platform_message("linux") is None


def test_resolve_log_level_accepts_known_level() -> None:
    level, warning_message = _resolve_log_level("warning")

    assert level == logging.WARNING
    assert warning_message is None


def test_resolve_log_level_warns_on_unknown_level() -> None:
    level, warning_message = _resolve_log_level("mystery")

    assert level == logging.INFO
    assert warning_message is not None
