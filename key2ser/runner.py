from __future__ import annotations

from dataclasses import dataclass, field
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
    
    @property
    def shift_active(self) -> bool:
        return bool(self.shift_keys)


class DeviceNotFoundError(RuntimeError):
    pass


def _match_device_info(device: InputDevice, *, vendor_id: int, product_id: int) -> bool:
    return device.info.vendor == vendor_id and device.info.product == product_id


def _select_device_by_vid_pid(devices: Iterable[str], vendor_id: int, product_id: int) -> InputDevice:
    matches = []
    for path in devices:
        device = InputDevice(path)
        if _match_device_info(device, vendor_id=vendor_id, product_id=product_id):
            matches.append(device)
    if not matches:
        raise DeviceNotFoundError("指定されたVID/PIDに一致する入力デバイスが見つかりません。")
    if len(matches) > 1:
        raise DeviceNotFoundError("VID/PIDが一致するデバイスが複数あります。deviceを指定してください。")
    return matches[0]


def open_input_device(config: InputConfig) -> InputDevice:
    if config.device:
        path = Path(config.device)
        if not path.exists():
            raise DeviceNotFoundError(f"input.device が存在しません: {path}")
        return InputDevice(str(path))

    if config.vendor_id is not None and config.product_id is not None:
        return _select_device_by_vid_pid(list_devices(), config.vendor_id, config.product_id)

    raise DeviceNotFoundError("input.device または vendor_id/product_id を指定してください。")


def _encode_payload(payload: str, encoding: str) -> bytes:
    try:
        return payload.encode(encoding)
    except UnicodeEncodeError as exc:
        raise ValueError("指定されたエンコーディングで変換できない文字が含まれています。") from exc


def _send_payload(port: serial.Serial, payload: str, encoding: str) -> None:
    data = _encode_payload(payload, encoding)
    port.write(data)
    port.flush()


def _reset_buffer(state: BufferState) -> None:
    state.text = ""
    state.last_input_time = None


def _iter_keycodes(key_event) -> Iterable[str]:
    return key_event.keycode if isinstance(key_event.keycode, list) else [key_event.keycode]


def _open_serial_port(config: AppConfig) -> serial.Serial:
    return serial.Serial(
        port=config.serial.port,
        baudrate=config.serial.baudrate,
        timeout=config.serial.timeout,
    )


def _log_device_info(device: InputDevice, config: AppConfig) -> None:
    logger.info("入力デバイス: %s", device.path)
    logger.info("シリアル送信先: %s", config.serial.port)


def _handle_key_down(
    keycode: str,
    state: BufferState,
    keymap: KeyMapper,
    line_end: str,
    send_on_enter: bool,
    send_mode: str,
) -> Optional[str]:
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
            state.last_input_time = time.monotonic()
    else:
        logger.debug("未対応キー: %s", keycode)
    return None


def _handle_key_up(keycode: str, state: BufferState) -> None:
    if keycode in SHIFT_KEYCODES:
        state.shift_keys.discard(keycode)


def _maybe_flush_idle_timeout(
    state: BufferState,
    *,
    line_end: str,
    idle_timeout_seconds: float,
    now: float,
) -> Optional[str]:
    if not state.text or state.last_input_time is None:
        return None
    if now - state.last_input_time < idle_timeout_seconds:
        return None
    payload = state.text + line_end
    state.text = ""
    state.last_input_time = None
    return payload


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
) -> None:
    if event.type != ecodes.EV_KEY:
        return
    key_event = categorize(event)
    keycodes = key_event.keycode if isinstance(key_event.keycode, list) else [key_event.keycode]
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
                _send_payload(port, payload, encoding)
    elif key_event.keystate == key_event.key_up:
        for keycode in _iter_keycodes(key_event):
            _handle_key_up(keycode, state)


def _run_event_loop_idle_timeout(config: AppConfig, device: InputDevice, *, keymap: KeyMapper) -> None:
    state = BufferState()
    with _open_serial_port(config) as port:
        _log_device_info(device, config)
        while True:
            if state.text and state.last_input_time is not None:
                now = time.monotonic()
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
                        _send_payload(port, payload, config.output.encoding)
                    continue
                timeout = remaining
            else:
                timeout = None
            # 入力待ちとタイムアウトを両立させるため、selectで監視する。
            readable, _, _ = select.select([device], [], [], timeout)
            if not readable:
                payload = _maybe_flush_idle_timeout(
                    state,
                    line_end=config.output.line_end,
                    idle_timeout_seconds=config.output.idle_timeout_seconds,
                    now=time.monotonic(),
                )
                if payload is not None:
                    _send_payload(port, payload, config.output.encoding)
                continue
            for event in device.read():
                _process_key_event(
                    event,
                    state=state,
                    keymap=keymap,
                    line_end=config.output.line_end,
                    send_on_enter=config.output.send_on_enter,
                    send_mode=config.output.send_mode,
                    port=port,
                    encoding=config.output.encoding,
                )


def _run_event_loop_default(config: AppConfig, device: InputDevice, *, keymap: KeyMapper) -> None:
    state = BufferState()
    with _open_serial_port(config) as port:
        _log_device_info(device, config)
        for event in device.read_loop():
            _process_key_event(
                event,
                state=state,
                keymap=keymap,
                line_end=config.output.line_end,
                send_on_enter=config.output.send_on_enter,
                send_mode=config.output.send_mode,
                port=port,
                encoding=config.output.encoding,
            )


def run_event_loop(config: AppConfig, *, keymap: KeyMapper = DEFAULT_KEYMAP) -> None:
    if config.input.mode != "evdev":
        raise ValueError("input.mode は evdev のみサポートしています。")

    device = open_input_device(config.input)
    if config.input.grab:
        device.grab()

    if config.output.send_mode == "idle_timeout":
        _run_event_loop_idle_timeout(config, device, keymap=keymap)
    else:
        _run_event_loop_default(config, device, keymap=keymap)
