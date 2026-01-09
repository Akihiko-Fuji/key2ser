from __future__ import annotations

from dataclasses import dataclass, field
import errno
import logging
from pathlib import Path
import select
import time
from typing import Iterable, Optional, Set

from evdev import InputDevice, categorize, ecodes, list_devices
import serial

from key2ser.config import AppConfig, InputConfig
from key2ser.keymap import DEFAULT_KEYMAP, KANA_TOGGLE_KEYCODES, SHIFT_KEYCODES, KeyMapper


logger = logging.getLogger(__name__)


@dataclass
class BufferState:
    text: str = ""
    shift_keys: Set[str] = field(default_factory=set)
    kana_mode: bool = False
    last_input_time: float | None = None
    last_sent_payload: str | None = None
    last_sent_time: float | None = None

    @property
    def shift_active(self) -> bool:
        return bool(self.shift_keys)


class DeviceNotFoundError(RuntimeError):
    pass


# 入力デバイスのVID/PID一致を判定する。
def _match_device_info(device: InputDevice, *, vendor_id: int, product_id: int) -> bool:
    """InputDeviceの情報が指定VID/PIDと一致するか判定する。"""
    return device.info.vendor == vendor_id and device.info.product == product_id

class DeviceAccessError(RuntimeError):
    pass


class SerialConnectionError(RuntimeError):
    pass


# VID/PIDで一致する入力デバイスを1つ選択する。
def _select_device_by_vid_pid(devices: Iterable[str], vendor_id: int, product_id: int) -> InputDevice:
    """候補のデバイスからVID/PID一致のものを検索して返す。"""
    matches = []
    access_error = False
    for path in devices:
        try:
            device = InputDevice(path)
        except OSError as exc:
            access_error = True
            logger.warning("入力デバイスのオープンに失敗しました: %s", path)
            logger.debug("入力デバイスの詳細エラー: %s", exc)
            continue
        if _match_device_info(device, vendor_id=vendor_id, product_id=product_id):
            matches.append(device)
    if not matches:
        # デバイス一覧の取得はできても個別アクセスに失敗した場合は別エラーにする。
        if access_error:
            raise DeviceAccessError("入力デバイスのオープンに失敗しました。")
        raise DeviceNotFoundError("指定されたVID/PIDに一致する入力デバイスが見つかりません。")
    if len(matches) > 1:
        # 複数ヒットは誤送信を避けるため明示指定を促す。
        raise DeviceNotFoundError("VID/PIDが一致するデバイスが複数あります。deviceを指定してください。")
    return matches[0]


# 設定に従って入力デバイスを開く。
def open_input_device(config: InputConfig) -> InputDevice:
    """デバイスパスまたはVID/PID指定でInputDeviceを取得する。"""
    if config.device:
        path = Path(config.device)
        if not path.exists():
            raise DeviceNotFoundError(f"input.device が存在しません: {path}")
        try:
            return InputDevice(str(path))
        except PermissionError as exc:
            raise DeviceAccessError("入力デバイスへのアクセス権限がありません。") from exc
        except OSError as exc:
            raise DeviceAccessError("入力デバイスのオープンに失敗しました。") from exc

    if config.vendor_id is not None and config.product_id is not None:
        try:
            devices = list_devices()
        except OSError as exc:
            raise DeviceAccessError("入力デバイス一覧の取得に失敗しました。") from exc
        return _select_device_by_vid_pid(devices, config.vendor_id, config.product_id)


    raise DeviceNotFoundError("input.device または vendor_id/product_id を指定してください。")


# 送信前の文字列を指定エンコーディングでバイト化する。
def _encode_payload(payload: str, encoding: str) -> bytes:
    """出力文字列をエンコードし、失敗時はValueErrorに変換する。"""
    try:
        return payload.encode(encoding)
    except UnicodeEncodeError as exc:
        raise ValueError("指定されたエンコーディングで変換できない文字が含まれています。") from exc

    except LookupError as exc:
        raise ValueError("output.encoding に未対応の文字コードが指定されています。") from exc

# シリアルへデータを送信する。
def _send_payload(port: serial.Serial, payload: str, encoding: str) -> None:
    """シリアルポートにペイロードを書き込み送信する。"""
    data = _encode_payload(payload, encoding)
    try:
        port.write(data)
        port.flush()
    except (serial.SerialException, OSError) as exc:
        raise SerialConnectionError("シリアルへの送信に失敗しました。") from exc

# 直近の送信と重複する場合に抑止するか判定
def _should_suppress_duplicate(
    state: BufferState,
    payload: str,
    *,
    dedup_window_seconds: float,
    now: float,
) -> bool:
    """直近の送信と重複する場合に抑止するか判定する。"""
    if dedup_window_seconds <= 0:
        return False
    if state.last_sent_payload is None or state.last_sent_time is None:
        return False
    if payload != state.last_sent_payload:
        return False
    return now - state.last_sent_time <= dedup_window_seconds


def _send_payload_with_dedup(
    port: serial.Serial,
    payload: str,
    *,
    state: BufferState,
    send_mode: str,
    encoding: str,
    dedup_window_seconds: float,
) -> None:
    """重複送信を抑止しながらペイロードを送信する。"""
    now = time.monotonic()
    if send_mode != "per_char":
        if _should_suppress_duplicate(
            state,
            payload,
            dedup_window_seconds=dedup_window_seconds,
            now=now,
        ):
            # バーコードリーダーの二重送信を抑止するため、短時間の同一ペイロードは無視する。
            return
    _send_payload(port, payload, encoding)
    state.last_sent_payload = payload
    state.last_sent_time = now


# 入力バッファを初期化する。
def _reset_buffer(state: BufferState) -> None:
    """送信後に状態を初期化するためのヘルパー。"""
    state.text = ""
    state.last_input_time = None


# キーイベントからキーコードのリストを取得する。
def _iter_keycodes(key_event) -> Iterable[str]:
    """単一/複数のキーコード表現を統一して返す。"""
    return key_event.keycode if isinstance(key_event.keycode, list) else [key_event.keycode]


# シリアルポートを開く。
def _open_serial_port(config: AppConfig) -> serial.Serial:
    """設定に従ってシリアルポートを開き例外を変換する。"""
    try:
        return serial.Serial(
            port=config.serial.port,
            baudrate=config.serial.baudrate,
            timeout=config.serial.timeout,
        )
    except (serial.SerialException, OSError) as exc:
        reason = str(exc)
        if isinstance(exc, OSError):
            if exc.errno == errno.ENOENT:
                reason = "デバイスが存在しません。"
            elif exc.errno == errno.EACCES:
                reason = "アクセス権限がありません。"
        raise SerialConnectionError(
            f"シリアルポートを開けませんでした: {config.serial.port} ({reason})"
        ) from exc


# 起動時にデバイス情報をログ出力する。
def _log_device_info(device: InputDevice, config: AppConfig) -> None:
    """入出力デバイスの情報をログに記録する。"""
    logger.info("入力デバイス: %s", device.path)
    logger.info("シリアル送信先: %s", config.serial.port)


# キーダウン時のバッファ処理と送信判定を行う。
def _handle_key_down(
    keycode: str,
    state: BufferState,
    keymap: KeyMapper,
    line_end: str,
    send_on_enter: bool,
    send_mode: str,
) -> Optional[str]:
    """キーダウンイベントを解釈して送信文字列を返す。"""
    if keycode in SHIFT_KEYCODES:
        state.shift_keys.add(keycode)
        return None
    if keycode in KANA_TOGGLE_KEYCODES:
        state.kana_mode = not state.kana_mode
        return None
    if keycode == "KEY_ENTER" and send_mode == "on_enter":
        # バーコードリーダーはEnterで終端することが多いため、ここでまとめて送信する。
        payload = state.text + line_end if state.text or send_on_enter else None
        _reset_buffer(state)
        if payload is not None:
            return payload
        return None
    if keycode == "KEY_BACKSPACE":
        if send_mode != "per_char":
            # 逐次送信でなければバッファから最後の1文字を削除する。
            state.text = state.text[:-1]
            if send_mode == "idle_timeout":
                state.last_input_time = time.monotonic()
        return None
    mapped = keymap.map_keycode(keycode, state.shift_active, kana=state.kana_mode)
    if mapped:
        if send_mode == "per_char":
            return mapped
        state.text += mapped
        if send_mode == "idle_timeout":
            # アイドルタイムアウト基準時刻を入力ごとに更新する。
            state.last_input_time = time.monotonic()
    else:
        logger.debug("未対応キー: %s", keycode)
    return None


# キーアップ時にシフト状態を更新する。
def _handle_key_up(keycode: str, state: BufferState) -> None:
    """キーアップでシフト状態を解除する。"""
    if keycode in SHIFT_KEYCODES:
        state.shift_keys.discard(keycode)


# アイドルタイムアウト時の送信可否を判定する。
def _maybe_flush_idle_timeout(
    state: BufferState,
    *,
    line_end: str,
    idle_timeout_seconds: float,
    now: float,
) -> Optional[str]:
    """一定時間入力が止まった場合に送信ペイロードを返す。"""
    if not state.text or state.last_input_time is None:
        return None
    if now - state.last_input_time < idle_timeout_seconds:
        return None
    payload = state.text + line_end
    state.text = ""
    state.last_input_time = None
    return payload


# キーイベントを解析して必要に応じて送信する。
def _process_key_event(
    event,
    *,
    state: BufferState,
    keymap: KeyMapper,
    line_end: str,
    send_on_enter: bool,
    send_mode: str,
    port: serial.Serial,
    encoding: str,
    dedup_window_seconds: float,
) -> None:
    """EV_KEYイベントのみを処理して送信する。"""
    if event.type != ecodes.EV_KEY:
        return
    key_event = categorize(event)
    if key_event.keystate == key_event.key_down:
        for keycode in _iter_keycodes(key_event):
            payload = _handle_key_down(
                keycode,
                state,
                keymap,
                line_end,
                send_on_enter,
                send_mode,
            )
            if payload is not None:
                _send_payload_with_dedup(
                    port,
                    payload,
                    state=state,
                    send_mode=send_mode,
                    encoding=encoding,
                    dedup_window_seconds=dedup_window_seconds,
                )
    elif key_event.keystate == key_event.key_up:
        for keycode in _iter_keycodes(key_event):
            _handle_key_up(keycode, state)


# アイドルタイムアウト方式のイベントループを実行する。
def _run_event_loop_idle_timeout(config: AppConfig, device: InputDevice, *, keymap: KeyMapper) -> None:
    """一定時間入力が止まったら送信するモードのループ。"""
    state = BufferState()
    with _open_serial_port(config) as port:
        _log_device_info(device, config)
        while True:
            if state.text and state.last_input_time is not None:
                now = time.monotonic()
                # 入力停止の残り時間を計算して待機時間に使う。
                remaining = config.output.idle_timeout_seconds - (now - state.last_input_time)
                if remaining <= 0:
                    # タイムアウトを超えたら入力待ちより先に送信して遅延を抑える。
                    payload = _maybe_flush_idle_timeout(
                        state,
                        line_end=config.output.line_end,
                        idle_timeout_seconds=config.output.idle_timeout_seconds,
                        now=now,
                    )
                    if payload is not None:
                        _send_payload_with_dedup(
                            port,
                            payload,
                            state=state,
                            send_mode=config.output.send_mode,
                            encoding=config.output.encoding,
                            dedup_window_seconds=config.output.dedup_window_seconds,
                        )
                    continue
                timeout = remaining
            else:
                timeout = None
            # 入力待ちとタイムアウトを両立させるため、selectで監視する。
            try:
                readable, _, _ = select.select([device], [], [], timeout)
            except OSError as exc:
                raise DeviceAccessError("入力デバイスの待機中にエラーが発生しました。") from exc
            if not readable:
                payload = _maybe_flush_idle_timeout(
                    state,
                    line_end=config.output.line_end,
                    idle_timeout_seconds=config.output.idle_timeout_seconds,
                    now=time.monotonic(),
                )
                if payload is not None:
                    _send_payload_with_dedup(
                        port,
                        payload,
                        state=state,
                        send_mode=config.output.send_mode,
                        encoding=config.output.encoding,
                        dedup_window_seconds=config.output.dedup_window_seconds,
                    )
                continue
            try:
                events = list(device.read())
            except OSError as exc:
                raise DeviceAccessError("入力デバイスの読み取りに失敗しました。") from exc
            for event in events:
                _process_key_event(
                    event,
                    state=state,
                    keymap=keymap,
                    line_end=config.output.line_end,
                    send_on_enter=config.output.send_on_enter,
                    send_mode=config.output.send_mode,
                    port=port,
                    encoding=config.output.encoding,
                    dedup_window_seconds=config.output.dedup_window_seconds,
                )


# 標準方式のイベントループを実行する。
def _run_event_loop_default(config: AppConfig, device: InputDevice, *, keymap: KeyMapper) -> None:
    """Enter送信/逐次送信向けのイベントループ。"""
    state = BufferState()
    with _open_serial_port(config) as port:
        _log_device_info(device, config)
        try:
            event_iterator = device.read_loop()
        except OSError as exc:
            raise DeviceAccessError("入力デバイスの監視を開始できませんでした。") from exc
        try:
            for event in event_iterator:
                _process_key_event(
                    event,
                    state=state,
                    keymap=keymap,
                    line_end=config.output.line_end,
                    send_on_enter=config.output.send_on_enter,
                    send_mode=config.output.send_mode,
                    port=port,
                    encoding=config.output.encoding,
                    dedup_window_seconds=config.output.dedup_window_seconds,
                )
        except OSError as exc:
            raise DeviceAccessError("入力デバイスの読み取りに失敗しました。") from exc


# 設定に応じて適切なイベントループを起動する。
def run_event_loop(config: AppConfig, *, keymap: KeyMapper = DEFAULT_KEYMAP) -> None:
    """入力モードに応じたイベントループを開始する。"""
    if config.input.mode != "evdev":
        raise ValueError("input.mode は evdev のみサポートしています。")

    device = open_input_device(config.input)
    if config.input.grab:
        try:
            device.grab()
        except OSError as exc:
            raise DeviceAccessError("入力デバイスの排他取得に失敗しました。") from exc

    # 送信モードに応じてループを切り替える。
    if config.output.send_mode == "idle_timeout":
        _run_event_loop_idle_timeout(config, device, keymap=keymap)
    else:
        _run_event_loop_default(config, device, keymap=keymap)
