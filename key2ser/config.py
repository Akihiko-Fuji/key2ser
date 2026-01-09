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
    line_end_mode: str
    send_on_enter: bool
    send_mode: str
    idle_timeout_seconds: float
    dedup_window_seconds: float


@dataclass(frozen=True)
class AppConfig:
    input: InputConfig
    serial: SerialConfig
    output: OutputConfig


DEFAULT_CONFIG_PATH = Path("config.ini")


# 任意指定の数値項目をintに変換する。
def _parse_optional_int(value: Optional[str], *, field_name: str) -> Optional[int]:
    """空文字やNoneを許容しつつ数値をパースする。"""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be integer (decimal or hex)") from exc


# 真偽値の設定をデフォルト込みで取得する。
def _get_bool(parser: configparser.ConfigParser, section: str, option: str, default: bool) -> bool:
    """存在しない設定項目に対して既定値を返す。"""
    if not parser.has_option(section, option):
        return default
    return parser.getboolean(section, option)


# 改行コードのエスケープ解釈を行う。
def _parse_line_end(line_end: str, *, line_end_mode: str) -> str:
    """改行モードに応じてエスケープ変換を適用する。"""
    if line_end_mode == "literal":
        return line_end
    try:
        return bytes(line_end, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError as exc:
        raise ValueError("output.line_end に無効なエスケープシーケンスがあります。") from exc


# 設定ファイルを読み込んでアプリ設定に変換する。
def load_config(path: Path) -> AppConfig:
    """config.ini を検証しながら AppConfig に変換する。"""
    parser = configparser.ConfigParser()
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    try:
        parser.read(path)
    except (configparser.Error, OSError, UnicodeDecodeError) as exc:
        raise ValueError("config.ini の読み取りに失敗しました。") from exc

    # 必須セクションが揃っているかを最初に確認する。
    required_sections = {"input", "serial", "output"}
    missing_sections = sorted(required_sections - set(parser.sections()))
    if missing_sections:
        missing_labels = ", ".join(missing_sections)
        raise ValueError(f"config.ini に必要なセクションがありません: {missing_labels}")

    # 入力デバイスの指定は名前優先だがVID/PIDにも対応する。
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

    # 送信方式に応じて改行や送信トリガーを決める。
    encoding = parser.get("output", "encoding", fallback="utf-8").strip()
    line_end_mode = parser.get("output", "line_end_mode", fallback="literal").strip().lower() or "literal"
    if line_end_mode not in {"literal", "escape"}:
        raise ValueError("output.line_end_mode は literal / escape のいずれかを指定してください。")
    line_end = parser.get("output", "line_end", fallback="\r\n")
    line_end = _parse_line_end(line_end, line_end_mode=line_end_mode)
    send_on_enter = _get_bool(parser, "output", "send_on_enter", True)
    send_mode = parser.get("output", "send_mode", fallback="on_enter").strip().lower() or "on_enter"
    if send_mode not in {"on_enter", "per_char", "idle_timeout"}:
        raise ValueError("output.send_mode は on_enter / per_char / idle_timeout のいずれかを指定してください。")
    idle_timeout_seconds = parser.getfloat("output", "idle_timeout_seconds", fallback=0.5)
    if idle_timeout_seconds < 0:
        raise ValueError("output.idle_timeout_seconds は 0 以上の値を指定してください。")
    dedup_window_seconds = parser.getfloat("output", "dedup_window_seconds", fallback=0.2)
    if dedup_window_seconds < 0:
        raise ValueError("output.dedup_window_seconds は 0 以上の値を指定してください。")

    return AppConfig(
        input=InputConfig(
            mode=mode,
            device=device,
            vendor_id=vendor_id,
            product_id=product_id,
            grab=grab,
        ),
        serial=SerialConfig(port=port, baudrate=baudrate, timeout=timeout),
        output=OutputConfig(
            encoding=encoding,
            line_end=line_end,
            line_end_mode=line_end_mode,
            send_on_enter=send_on_enter,
            send_mode=send_mode,
            idle_timeout_seconds=idle_timeout_seconds,
            dedup_window_seconds=dedup_window_seconds,
        ),
    )
