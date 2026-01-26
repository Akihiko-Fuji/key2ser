import configparser
import logging
from pathlib import Path

import pytest

from key2ser.config import (
    load_config,
    DEFAULT_PREFERRED_INPUT_KEYS,
    DEFAULT_TERMINATOR_KEYS,
)



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
    assert config.input.device_name_contains is None
    assert config.input.prefer_event_has_keys == DEFAULT_PREFERRED_INPUT_KEYS
    assert config.input.reconnect_interval_seconds == 3.0
    assert config.serial.port == "/dev/ttyV0"
    assert config.serial.bytesize == 8
    assert config.serial.parity == "N"
    assert config.serial.stopbits == 1.0
    assert config.serial.write_timeout is None
    assert config.serial.emulate_modem_signals is False
    assert config.serial.exclusive is None
    assert config.serial.dtr is None
    assert config.serial.rts is None
    assert config.serial.emulate_timing is False
    assert config.serial.pty_link is None
    assert config.serial.pty_mode is None
    assert config.serial.pty_group is None
    assert config.output.encoding_errors == "strict"
    assert config.output.terminator_keys == DEFAULT_TERMINATOR_KEYS
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


def test_load_config_rejects_partial_vid_pid(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev
vendor_id=0x1234

[serial]
port=/dev/ttyV0

[output]
encoding=utf-8
line_end=\r\n
""".strip()
    )

    with pytest.raises(ValueError, match="input.vendor_id と input.product_id は両方指定してください。"):
        load_config(config_file)


def test_load_config_rejects_partial_product_id(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev
product_id=0xabcd

[serial]
port=/dev/ttyV0

[output]
encoding=utf-8
line_end=\r\n
""".strip()
    )

    with pytest.raises(ValueError, match="input.vendor_id と input.product_id は両方指定してください。"):
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


def test_load_config_parses_serial_settings(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev
device_name_contains=Scanner
prefer_event_has_keys=KEY_A, KEY_B

[serial]
port=/dev/ttyV0
bytesize=7
parity=even
stopbits=2
xonxoff=true
rtscts=true
dsrdtr=true
exclusive=true
emulate_modem_signals=true
write_timeout=0.2
dtr=false
rts=true
pty_link=/dev/ttyV1
pty_mode=660
pty_group=dialout
emulate_timing=true

[output]
encoding=utf-8
encoding_errors=replace
line_end=\r\n
""".strip()
    )

    config = load_config(config_file)

    assert config.serial.bytesize == 7
    assert config.serial.parity == "E"
    assert config.serial.stopbits == 2.0
    assert config.serial.write_timeout == 0.2
    assert config.serial.xonxoff is True
    assert config.serial.rtscts is True
    assert config.serial.dsrdtr is True
    assert config.serial.emulate_modem_signals is True
    assert config.serial.exclusive is True
    assert config.serial.dtr is False
    assert config.serial.rts is True
    assert config.serial.emulate_timing is True
    assert config.serial.pty_link == "/dev/ttyV1"
    assert config.serial.pty_mode == 0o660
    assert config.serial.pty_group == "dialout"
    assert config.output.encoding_errors == "replace"
    assert config.input.device_name_contains == "Scanner"
    assert config.input.prefer_event_has_keys == ("KEY_A", "KEY_B")


def test_load_config_allows_empty_optional_bools(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0
exclusive=
dtr=
rts=

[output]
encoding=utf-8
line_end=\r\n
""".strip()
    )

    config = load_config(config_file)

    assert config.serial.exclusive is None
    assert config.serial.dtr is None
    assert config.serial.rts is None


def test_load_config_parses_terminator_keys(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0

[output]
terminator_keys=KEY_ENTER, KEY_KPENTER
""".strip()
    )

    config = load_config(config_file)

    assert config.output.terminator_keys == ("KEY_ENTER", "KEY_KPENTER")

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


def test_load_config_rejects_invalid_encoding_errors(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0

[output]
encoding_errors=unknown
""".strip()
    )

    with pytest.raises(ValueError, match="output.encoding_errors は strict/replace/ignore"):
        load_config(config_file)


def test_load_config_rejects_invalid_encoding(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0

[output]
encoding=unknown-encoding
""".strip()
    )

    with pytest.raises(ValueError, match="output.encoding に未対応の文字コードが指定されています。"):
        load_config(config_file)


def test_load_config_rejects_negative_write_timeout(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0
write_timeout=-1

[output]
encoding=utf-8
line_end=\\r\\n
""".strip()
    )

    with pytest.raises(ValueError, match="serial.write_timeout は 0 以上の値を指定してください。"):
        load_config(config_file)


def test_load_config_rejects_invalid_parity(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0
parity=invalid

[output]
encoding=utf-8
line_end=\r\n
""".strip()
    )

    with pytest.raises(ValueError, match="serial.parity は none/odd/even/mark/space のいずれかを指定してください。"):
        load_config(config_file)


def test_load_config_rejects_invalid_baudrate(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0
baudrate=0

[output]
encoding=utf-8
line_end=\r\n
""".strip()
    )

    with pytest.raises(ValueError, match="serial.baudrate は 1 以上の値を指定してください。"):
        load_config(config_file)


def test_load_config_rejects_invalid_stopbits(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev

[serial]
port=/dev/ttyV0
stopbits=3

[output]
encoding=utf-8
line_end=\r\n
""".strip()
    )

    with pytest.raises(ValueError, match="serial.stopbits は 1/1.5/2 のいずれかを指定してください。"):
        load_config(config_file)


def test_load_config_rejects_negative_reconnect_interval(tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev
reconnect_interval_seconds=-1

[serial]
port=/dev/ttyV0

[output]
encoding=utf-8
line_end=\\r\\n
""".strip()
    )

    with pytest.raises(ValueError, match="input.reconnect_interval_seconds は 0 以上の値を指定してください。"):
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


def test_load_config_warns_on_unknown_key_names(caplog, tmp_path: Path) -> None:
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        """
[input]
mode=evdev
prefer_event_has_keys=KEY_A, KEY_BOGUS

[serial]
port=/dev/ttyV0

[output]
terminator_keys=KEY_ENTER, KEY_BAD
""".strip()
    )

    caplog.set_level(logging.WARNING)

    load_config(config_file)

    assert "input.prefer_event_has_keys に未対応のキーが含まれています" in caplog.text
    assert "KEY_BOGUS" in caplog.text
    assert "output.terminator_keys に未対応のキーが含まれています" in caplog.text
    assert "KEY_BAD" in caplog.text
