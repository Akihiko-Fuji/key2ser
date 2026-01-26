import logging

import pytest

from key2ser import cli


def test_unsupported_platform_message_for_windows() -> None:
    assert cli._unsupported_platform_message("win32") is not None


def test_unsupported_platform_message_for_linux() -> None:
    assert cli._unsupported_platform_message("linux") is None


def test_resolve_log_level_accepts_known_level() -> None:
    level, warning_message = cli._resolve_log_level("warning")

    assert level == logging.WARNING
    assert warning_message is None


def test_resolve_log_level_warns_on_unknown_level() -> None:
    level, warning_message = cli._resolve_log_level("mystery")

    assert level == logging.INFO
    assert warning_message is not None


def test_main_returns_exit_code_on_config_error(monkeypatch) -> None:
    def raise_config_error(_path):
        raise ValueError("bad config")

    monkeypatch.setattr(cli, "load_config", raise_config_error)
    monkeypatch.setattr(cli.sys, "platform", "linux")

    assert cli.main([]) == 4


def test_main_returns_exit_code_on_serial_error(monkeypatch) -> None:
    from key2ser import runner

    def raise_serial_error(_config) -> None:
        raise runner.SerialConnectionError("serial failed")

    monkeypatch.setattr(cli, "load_config", lambda _path: object())
    monkeypatch.setattr(runner, "run_event_loop", raise_serial_error)
    monkeypatch.setattr(cli.sys, "platform", "linux")

    assert cli.main([]) == 5


def test_main_passes_unhandled_error(monkeypatch) -> None:
    from key2ser import runner

    def raise_unhandled(_config) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "load_config", lambda _path: object())
    monkeypatch.setattr(runner, "run_event_loop", raise_unhandled)
    monkeypatch.setattr(cli.sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="boom"):
        cli.main([])
