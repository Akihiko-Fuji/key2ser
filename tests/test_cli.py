from key2ser.cli import _unsupported_platform_message


def test_unsupported_platform_message_for_windows() -> None:
    assert _unsupported_platform_message("win32") is not None


def test_unsupported_platform_message_for_linux() -> None:
    assert _unsupported_platform_message("linux") is None
