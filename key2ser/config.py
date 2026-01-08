from __future__ import annotations

from dataclasses import dataclass
import configparser
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class InputConfig:
    mode: str
    device: Optional[str]
    vendor_id: Optional[int]
    product_id: Optional[int]
    grab: bool


@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int
    timeout: float


@dataclass(frozen=True)
class OutputConfig:
    encoding: str
    line_end: str
    send_on_enter: bool


@dataclass(frozen=True)
class AppConfig:
    input: InputConfig
    serial: SerialConfig
    output: OutputConfig


DEFAULT_CONFIG_PATH = Path("config.ini")


def _parse_optional_int(value: Optional[str], *, field_name: str) -> Optional[int]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be integer (decimal or hex)") from exc


def _get_bool(parser: configparser.ConfigParser, section: str, option: str, default: bool) -> bool:
    if not parser.has_option(section, option):
        return default
    return parser.getboolean(section, option)


def load_config(path: Path) -> AppConfig:
    parser = configparser.ConfigParser()
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    parser.read(path)

    mode = parser.get("input", "mode", fallback="evdev").strip()
    device = parser.get("input", "device", fallback="").strip() or None
    vendor_id = _parse_optional_int(parser.get("input", "vendor_id", fallback=None), field_name="vendor_id")
    product_id = _parse_optional_int(parser.get("input", "product_id", fallback=None), field_name="product_id")
    grab = _get_bool(parser, "input", "grab", False)

    port = parser.get("serial", "port", fallback="").strip()
    if not port:
        raise ValueError("serial.port is required")
    baudrate = parser.getint("serial", "baudrate", fallback=9600)
    timeout = parser.getfloat("serial", "timeout", fallback=1.0)

    encoding = parser.get("output", "encoding", fallback="utf-8").strip()
    line_end = parser.get("output", "line_end", fallback="\r\n")
    send_on_enter = _get_bool(parser, "output", "send_on_enter", True)

    return AppConfig(
        input=InputConfig(
            mode=mode,
            device=device,
            vendor_id=vendor_id,
            product_id=product_id,
            grab=grab,
        ),
        serial=SerialConfig(port=port, baudrate=baudrate, timeout=timeout),
        output=OutputConfig(encoding=encoding, line_end=line_end, send_on_enter=send_on_enter),
    )
