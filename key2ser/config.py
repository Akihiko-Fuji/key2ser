from __future__ import annotations

from dataclasses import dataclass
import configparser
from pathlib import Path
import re
from typing import Optional


@dataclass(frozen=True)
class InputConfig:
    mode: str
    device: Optional[str]
    vendor_id: Optional[int]
    product_id: Optional[int]
    device_name_contains: Optional[str]
    prefer_event_has_keys: tuple[str, ...]
    grab: bool
    reconnect_interval_seconds: float
    

@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int
    timeout: float
    write_timeout: Optional[float]
    bytesize: int
    parity: str
    stopbits: float
    xonxoff: bool
    rtscts: bool
    dsrdtr: bool
    exclusive: Optional[bool]
    emulate_modem_signals: bool
    dtr: Optional[bool]
    rts: Optional[bool]
    emulate_timing: bool
    pty_link: Optional[str]
    pty_mode: Optional[int]
    pty_group: Optional[str]

@dataclass(frozen=True)
class OutputConfig:
    encoding: str
    encoding_errors: str
    line_end: str
    line_end_mode: str
    terminator_keys: tuple[str, ...]
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
DEFAULT_PREFERRED_INPUT_KEYS = (
    "KEY_ENTER",
    "KEY_KPENTER",
    *tuple(f"KEY_{digit}" for digit in range(10)),
)
DEFAULT_TERMINATOR_KEYS = ("KEY_ENTER", "KEY_KPENTER")

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


# 任意指定の数値項目をfloatに変換する。
def _parse_optional_float(value: Optional[str], *, field_name: str) -> Optional[float]:
    """空文字やNoneを許容しつつ小数値をパースする。"""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be float") from exc


# 任意指定の真偽値を取得する。
def _parse_optional_bool(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
) -> Optional[bool]:
    """指定されていない場合はNoneを返す。"""
    if not parser.has_option(section, option):
        return None
    value = parser.get(section, option, fallback=None)
    if value is None:
        return None
    # 空文字は未指定として扱い、既存の設定ファイルの落とし穴を回避する。
    if not value.strip():
        return None
    return parser.getboolean(section, option)


# 真偽値の設定をデフォルト込みで取得する。
def _get_bool(parser: configparser.ConfigParser, section: str, option: str, default: bool) -> bool:
    """存在しない設定項目に対して既定値を返す。"""
    if not parser.has_option(section, option):
        return default
    return parser.getboolean(section, option)


# シリアルのデータビットをパースする。
def _parse_bytesize(value: int) -> int:
    """シリアルのデータビット設定を検証する。"""
    if value not in {5, 6, 7, 8}:
        raise ValueError("serial.bytesize は 5/6/7/8 のいずれかを指定してください。")
    return value


# シリアルのパリティをパースする。
def _parse_parity(value: str) -> str:
    """パリティ設定を正規化して返す。"""
    normalized = value.strip().lower()
    mapping = {
        "n": "N",
        "none": "N",
        "e": "E",
        "even": "E",
        "o": "O",
        "odd": "O",
        "m": "M",
        "mark": "M",
        "s": "S",
        "space": "S",
    }
    if normalized not in mapping:
        raise ValueError("serial.parity は none/odd/even/mark/space のいずれかを指定してください。")
    return mapping[normalized]


# シリアルのストップビットをパースする。
def _parse_stopbits(value: str) -> float:
    """ストップビット設定を検証する。"""
    try:
        stopbits = float(value)
    except ValueError as exc:
        raise ValueError("serial.stopbits は 1/1.5/2 のいずれかを指定してください。") from exc
    if stopbits not in {1.0, 1.5, 2.0}:
        raise ValueError("serial.stopbits は 1/1.5/2 のいずれかを指定してください。")
    return stopbits


# PTYのパーミッションをパースする。
def _parse_optional_mode(value: Optional[str]) -> Optional[int]:
    """PTYのパーミッションを数値に変換する。"""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if not re.fullmatch(r"[0-7]+", normalized):
        raise ValueError("serial.pty_mode は 0-7 の数字のみで指定してください。")
    return int(normalized, 8)


# 改行コードのエスケープ解釈を行う。
def _parse_line_end(line_end: str, *, line_end_mode: str) -> str:
    """改行モードに応じてエスケープ変換を適用する。"""
    if line_end_mode == "literal":
        return line_end
    try:
        return bytes(line_end, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError as exc:
        raise ValueError("output.line_end に無効なエスケープシーケンスがあります。") from exc

# カンマ区切りのキー一覧をパースする。
def _parse_key_list(value: Optional[str], *, default: Optional[tuple[str, ...]]) -> tuple[str, ...]:
    """キーコードのリスト設定を正規化して返す。"""
    if value is None:
        return default or tuple()
    stripped = value.strip()
    if not stripped:
        return tuple()
    keys = [item.strip().upper() for item in stripped.split(",") if item.strip()]
    return tuple(keys)


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
    device_name_contains = parser.get("input", "device_name_contains", fallback="").strip() or None
    prefer_event_has_keys = _parse_key_list(
        parser.get("input", "prefer_event_has_keys", fallback=None),
        default=DEFAULT_PREFERRED_INPUT_KEYS,
    )
    grab = _get_bool(parser, "input", "grab", False)
    reconnect_interval_seconds = parser.getfloat("input", "reconnect_interval_seconds", fallback=3.0)
    if reconnect_interval_seconds < 0:
        raise ValueError("input.reconnect_interval_seconds は 0 以上の値を指定してください。")

    port = parser.get("serial", "port", fallback="").strip()
    if not port:
        raise ValueError("serial.port is required")
    baudrate = parser.getint("serial", "baudrate", fallback=9600)
    if baudrate <= 0:
        raise ValueError("serial.baudrate は 1 以上の値を指定してください。")
    timeout = parser.getfloat("serial", "timeout", fallback=1.0)
    write_timeout = _parse_optional_float(parser.get("serial", "write_timeout", fallback=None), field_name="write_timeout")
    if write_timeout is not None and write_timeout < 0:
        raise ValueError("serial.write_timeout は 0 以上の値を指定してください。")
    bytesize = _parse_bytesize(parser.getint("serial", "bytesize", fallback=8))
    parity = _parse_parity(parser.get("serial", "parity", fallback="none"))
    stopbits = _parse_stopbits(parser.get("serial", "stopbits", fallback="1"))
    xonxoff = _get_bool(parser, "serial", "xonxoff", False)
    rtscts = _get_bool(parser, "serial", "rtscts", False)
    dsrdtr = _get_bool(parser, "serial", "dsrdtr", False)
    exclusive = _parse_optional_bool(parser, "serial", "exclusive")
    emulate_modem_signals = _get_bool(parser, "serial", "emulate_modem_signals", False)
    dtr = _parse_optional_bool(parser, "serial", "dtr")
    rts = _parse_optional_bool(parser, "serial", "rts")
    emulate_timing = _get_bool(parser, "serial", "emulate_timing", False)
    pty_link = parser.get("serial", "pty_link", fallback="").strip() or None
    pty_mode = _parse_optional_mode(parser.get("serial", "pty_mode", fallback=None))
    pty_group = parser.get("serial", "pty_group", fallback="").strip() or None
   
    # 送信方式に応じて改行や送信トリガーを決める。
    encoding = parser.get("output", "encoding", fallback="utf-8").strip()
    encoding_errors = parser.get("output", "encoding_errors", fallback="strict").strip().lower() or "strict"
    valid_encoding_errors = {
        "strict",
        "replace",
        "ignore",
        "backslashreplace",
        "xmlcharrefreplace",
        "namereplace",
    }
    if encoding_errors not in valid_encoding_errors:
        raise ValueError(
            "output.encoding_errors は strict/replace/ignore/backslashreplace/xmlcharrefreplace/namereplace のいずれかを指定してください。"
        )
    line_end_mode = parser.get("output", "line_end_mode", fallback="literal").strip().lower() or "literal"
    if line_end_mode not in {"literal", "escape"}:
        raise ValueError("output.line_end_mode は literal / escape のいずれかを指定してください。")
    line_end = parser.get("output", "line_end", fallback="\r\n")
    line_end = _parse_line_end(line_end, line_end_mode=line_end_mode)
    terminator_keys = _parse_key_list(
        parser.get("output", "terminator_keys", fallback=None),
        default=DEFAULT_TERMINATOR_KEYS,
    )
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
            device_name_contains=device_name_contains,
            prefer_event_has_keys=prefer_event_has_keys,
            grab=grab,
            reconnect_interval_seconds=reconnect_interval_seconds,
        ),
        serial=SerialConfig(
            port=port,
            baudrate=baudrate,
            timeout=timeout,
            write_timeout=write_timeout,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            dsrdtr=dsrdtr,
            exclusive=exclusive,
            emulate_modem_signals=emulate_modem_signals,
            dtr=dtr,
            rts=rts,
            emulate_timing=emulate_timing,
            pty_link=pty_link,
            pty_mode=pty_mode,
            pty_group=pty_group,
        ),
        output=OutputConfig(
            encoding=encoding,
            encoding_errors=encoding_errors,
            line_end=line_end,
            line_end_mode=line_end_mode,
            terminator_keys=terminator_keys,
            send_on_enter=send_on_enter,
            send_mode=send_mode,
            idle_timeout_seconds=idle_timeout_seconds,
            dedup_window_seconds=dedup_window_seconds,
        ),
    )
