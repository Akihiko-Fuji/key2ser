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
