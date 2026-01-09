import configparser
from pathlib import Path

import pytest

from key2ser.config import load_config


def test_load_config_parses_hex_vid_pid(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev
vendor_id=0x1234
product_id=0xabcd

[serial]
port=/dev/ttyV0
baudrate=9600

[output]
encoding=utf-8
line_end=\r\n
""".strip()
    )

    config = load_config(config_file)

    assert config.input.vendor_id == 0x1234
    assert config.input.product_id == 0xABCD
    assert config.serial.port == "/dev/ttyV0"
    assert config.output.dedup_window_seconds == 0.2


def test_load_config_requires_serial_port(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
baudrate=9600

[output]
encoding=utf-8
line_end=\r\n
""".strip()
    )

    with pytest.raises(ValueError, match="serial.port is required"):
        load_config(config_file)


def test_load_config_supports_line_end_escape(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0

[output]
line_end_mode=escape
line_end=\\r\\n
""".strip()
    )

    config = load_config(config_file)

    assert config.output.line_end == "\r\n"


def test_load_config_rejects_negative_dedup_window(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0

[output]
dedup_window_seconds=-1
""".strip()
    )

    with pytest.raises(ValueError, match="output.dedup_window_seconds は 0 以上の値を指定してください。"):
        load_config(config_file)


def test_load_config_handles_parse_error(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text("[input]\nmode=evdev\n")

    def raise_parse_error(_self, _path):
        raise configparser.MissingSectionHeaderError("config.ini", 1, "broken")

    monkeypatch.setattr(configparser.ConfigParser, "read", raise_parse_error)

    with pytest.raises(ValueError, match="config.ini の読み取りに失敗しました。"):
        load_config(config_file)


def test_load_config_requires_sections(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0
""".strip()
    )

    with pytest.raises(ValueError, match="config.ini に必要なセクションがありません"):
        load_config(config_file)


def test_load_config_handles_os_error(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text("[input]\nmode=evdev\n")

    def raise_os_error(_self, _path):
        raise OSError("permission denied")

    monkeypatch.setattr(configparser.ConfigParser, "read", raise_os_error)

    with pytest.raises(ValueError, match="config.ini の読み取りに失敗しました。"):
        load_config(config_file)
