from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
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


def _handle_key_down(
    keycode: str,
    state: BufferState,
    keymap: KeyMapper,
    line_end: str,
    send_on_enter: bool,
) -> Optional[str]:
    if keycode in SHIFT_KEYCODES:
        state.shift_keys.add(keycode)
        return None
    if keycode in KANA_TOGGLE_KEYCODES:
        state.kana_mode = not state.kana_mode
        return None
    if keycode == "KEY_ENTER":
        # バーコードリーダーはEnterで終端することが多いため、ここでまとめて送信する。
        payload = state.text + line_end
        if state.text or send_on_enter:
            state.text = ""
            return payload
        state.text = ""
        return None
    if keycode == "KEY_BACKSPACE":
        state.text = state.text[:-1]
        return None
    mapped = keymap.map_keycode(keycode, state.shift_active, kana=state.kana_mode)
    if mapped:
        state.text += mapped
    else:
        logger.debug("未対応キー: %s", keycode)
    return None


def _handle_key_up(keycode: str, state: BufferState) -> None:
    if keycode in SHIFT_KEYCODES:
        state.shift_keys.discard(keycode)


def run_event_loop(config: AppConfig, *, keymap: KeyMapper = DEFAULT_KEYMAP) -> None:
    if config.input.mode != "evdev":
        raise ValueError("input.mode は evdev のみサポートしています。")

    device = open_input_device(config.input)
    if config.input.grab:
        device.grab()

    state = BufferState()
    with serial.Serial(
        port=config.serial.port,
        baudrate=config.serial.baudrate,
        timeout=config.serial.timeout,
    ) as port:
        logger.info("入力デバイス: %s", device.path)
        logger.info("シリアル送信先: %s", config.serial.port)
        for event in device.read_loop():
            if event.type != ecodes.EV_KEY:
                continue
            key_event = categorize(event)
            keycodes = key_event.keycode if isinstance(key_event.keycode, list) else [key_event.keycode]
            if key_event.keystate == key_event.key_down:
                for keycode in keycodes:
                    payload = _handle_key_down(
                        keycode,
                        state,
                        keymap,
                        config.output.line_end,
                        config.output.send_on_enter,
                    )
                    if payload is not None:
                        _send_payload(port, payload, config.output.encoding)
            elif key_event.keystate == key_event.key_up:
                for keycode in keycodes:
                    _handle_key_up(keycode, state)
