from __future__ import annotations

from dataclasses import dataclass, field
import errno
import grp
import logging
import os
from pathlib import Path
import pty
import select
import threading
import time
import tty
from typing import Iterable, Optional, Set

from evdev import InputDevice, categorize, ecodes, list_devices
import serial

from key2ser.config import AppConfig, InputConfig, OutputConfig, SerialConfig
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


class PayloadEncodeError(RuntimeError):
    pass


# 入力デバイスのVID/PID一致を判定する。
def _match_device_info(device: InputDevice, *, vendor_id: int, product_id: int) -> bool:
    """InputDeviceの情報が指定VID/PIDと一致するか判定する。"""
    return device.info.vendor == vendor_id and device.info.product == product_id

class DeviceAccessError(RuntimeError):
    pass


class SerialConnectionError(RuntimeError):
    pass

@dataclass
class VirtualPtyResources:
    bridge: "VirtualPtyBridge"
    symlink_path: Optional[Path]
    created_symlink: bool
    app_slave: str
    peer_slave: str

    def close(self) -> None:
        """生成したPTYリソースを開放する。"""
        self.bridge.close()
        if self.created_symlink and self.symlink_path is not None:
            try:
                self.symlink_path.unlink()
            except FileNotFoundError:
                return
            except OSError as exc:
                logger.warning("シンボリックリンクの削除に失敗しました: %s", exc)


@dataclass
class SerialPortHandle:
    port: serial.Serial
    display_port: str
    resources: Optional[VirtualPtyResources] = None

    def __enter__(self) -> "SerialPortHandle":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.resources is not None:
            self.resources.close()
        self.port.close()
        return False

    def __getattr__(self, name: str):
        return getattr(self.port, name)


class VirtualPtyBridge:
    def __init__(self, master_a: int, master_b: int) -> None:
        self._master_a = master_a
        self._master_b = master_b
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        for fd in (self._master_a, self._master_b):
            try:
                os.close(fd)
            except OSError:
                continue
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                readable, _, _ = select.select([self._master_a, self._master_b], [], [], 0.5)
            except OSError as exc:
                if self._stop_event.is_set():
                    return
                logger.warning("仮想TTYのブリッジ監視に失敗しました: %s", exc)
                continue
            for fd in readable:
                try:
                    data = os.read(fd, 1024)
                except OSError as exc:
                    if not self._stop_event.is_set():
                        logger.warning("仮想TTYの読み取りに失敗しました: %s", exc)
                    continue
                if not data:
                    continue
                target = self._master_b if fd == self._master_a else self._master_a
                try:
                    os.write(target, data)
                except OSError as exc:
                    if not self._stop_event.is_set():
                        logger.warning("仮想TTYの書き込みに失敗しました: %s", exc)


# 起動時に利用可能な入力デバイスを列挙する。
def _log_available_devices() -> None:
    """接続済みの入力デバイスをログに出力する。"""
    try:
        devices = list_devices()
    except OSError as exc:
        logger.warning("入力デバイス一覧の取得に失敗しました: %s", exc)
        return
    if not devices:
        logger.info("入力デバイスが見つかりませんでした。")
        return
    logger.info("検出された入力デバイス:")
    for path in devices:
        try:
            device = InputDevice(path)
        except OSError as exc:
            logger.warning("入力デバイスの取得に失敗しました: %s (%s)", path, exc)
            continue
        try:
            logger.info(
                "- %s (VID=0x%04X PID=0x%04X 名称=%s)",
                device.path,
                device.info.vendor,
                device.info.product,
                device.name,
            )
        finally:
            # デバイス列挙時もFDリークを避けるため明示的にクローズする。
            _close_input_device(device)


# 入力デバイスのヒント文字列を正規化する。
def _normalize_device_hint(value: Optional[str]) -> str:
    """比較のために小文字化した文字列を返す。"""
    return value.lower() if value else ""


# 入力デバイスが指定キーを持つか判定する。
def _device_has_keys(device: InputDevice, keys: Iterable[str]) -> bool:
    """EV_KEYの対応キーに指定キーが含まれるか確認する。"""
    try:
        caps = device.capabilities().get(ecodes.EV_KEY, [])
    except OSError as exc:
        logger.debug("入力デバイスのcapabilities取得に失敗しました: %s", exc)
        return False
    if not caps:
        return False
    key_set = set(caps)
    for key in keys:
        code = getattr(ecodes, key, None)
        if code is not None and code in key_set:
            return True
    return False


# 入力デバイスのスコアリングを行う。
def _score_device(device: InputDevice, config: InputConfig) -> int:
    """デバイス選択のためのスコアを計算する。"""
    score = 0
    if config.prefer_event_has_keys and _device_has_keys(device, config.prefer_event_has_keys):
        score += 2
    if config.device_name_contains:
        hint = config.device_name_contains.lower()
        name = _normalize_device_hint(getattr(device, "name", None))
        phys = _normalize_device_hint(getattr(device, "phys", None))
        uniq = _normalize_device_hint(getattr(device, "uniq", None))
        if hint in name or hint in phys or hint in uniq:
            score += 1
    return score


def _select_single_device(matches: list[InputDevice], config: InputConfig) -> Optional[InputDevice]:
    """複数候補からスコア優先で1件を選択する。"""
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    scored = [(device, _score_device(device, config)) for device in matches]
    scored.sort(key=lambda item: item[1], reverse=True)
    best_device, best_score = scored[0]
    if logger.isEnabledFor(logging.DEBUG):
        # スコアリング結果をログに残すことで選別不能時の判断材料を確保する。
        summaries = ", ".join(f"{device.path}:{score}" for device, score in scored)
        logger.debug("入力デバイス候補のスコア: %s", summaries)
    if best_score > 0 and scored[1][1] < best_score:
        for device, _score in scored:
            if device is not best_device:
                _close_input_device(device)
        # 似たHIDが複数あると誤送信の恐れがあるため、有意差がある場合のみ自動選別する。
        logger.info("入力デバイスを自動選別しました: %s", best_device.path)
        return best_device
    if best_score > 0:
        logger.info("入力デバイスの候補が複数あり自動選別できませんでした。")
    return None



# VID/PIDで一致する入力デバイスを1つ選択する。
def _select_device_by_vid_pid(devices: Iterable[str], config: InputConfig) -> InputDevice:
    """候補のデバイスからVID/PID一致のものを検索して返す。"""
    matches: list[InputDevice] = []
    access_error = False
    for path in devices:
        try:
            device = InputDevice(path)
        except OSError as exc:
            access_error = True
            logger.warning("入力デバイスのオープンに失敗しました: %s", path)
            logger.debug("入力デバイスの詳細エラー: %s", exc)
            continue
        if _match_device_info(device, vendor_id=config.vendor_id, product_id=config.product_id):
            matches.append(device)
        else:
            # 非一致のデバイスは保持しないためクローズする。
            _close_input_device(device)
    if not matches:
        # デバイス一覧の取得はできても個別アクセスに失敗した場合は別エラーにする。
        if access_error:
            raise DeviceAccessError("入力デバイスのオープンに失敗しました。")
        raise DeviceNotFoundError("指定されたVID/PIDに一致する入力デバイスが見つかりません。")
    selected = _select_single_device(matches, config)
    if selected is not None:
        return selected
    # 複数ヒットは誤送信を避けるため明示指定を促す。
    for device in matches:
        _close_input_device(device)
    raise DeviceNotFoundError("VID/PIDが一致するデバイスが複数あります。deviceを指定してください。")


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
        return _select_device_by_vid_pid(devices, config)


    raise DeviceNotFoundError("input.device または vendor_id/product_id を指定してください。")


# 送信前の文字列を指定エンコーディングでバイト化する。
def _encode_payload(payload: str, encoding: str, *, errors: str) -> bytes:
    """出力文字列をエンコードし、失敗時はValueErrorに変換する。"""
    try:
        return payload.encode(encoding, errors=errors)
    except UnicodeEncodeError as exc:
        raise PayloadEncodeError("指定されたエンコーディングで変換できない文字が含まれています。") from exc
    except LookupError as exc:
        raise ValueError("output.encoding に未対応の文字コードが指定されています。") from exc

# シリアルのフレーム時間（1バイト分の転送時間）を求める。
def _calculate_frame_seconds(serial_config: SerialConfig) -> float:
    """通信設定に応じた1バイトの伝送時間を返す。"""
    parity_bits = 0 if serial_config.parity == "N" else 1
    bits_per_frame = 1 + serial_config.bytesize + parity_bits + serial_config.stopbits
    return bits_per_frame / serial_config.baudrate


# シリアルの通信速度に合わせて1バイトずつ送信する
def _send_payload_with_timing(
    port: serial.Serial,
    payload: str,
    *,
    encoding: str,
    encoding_errors: str,
    serial_config: SerialConfig,
) -> None:
    """シリアルの通信速度に合わせて1バイトずつ送信する。"""
    try:
        data = _encode_payload(payload, encoding, errors=encoding_errors)
    except PayloadEncodeError as exc:
        logger.warning("%s", exc)
        return
    if not data:
        return
    frame_seconds = _calculate_frame_seconds(serial_config)
    if frame_seconds <= 0:
        _send_payload(port, payload, encoding, encoding_errors=encoding_errors)
        return
    # 仮想TTYは通信速度の制約がないため、実機に近づける目的で送信間隔を制御する。
    next_time = time.monotonic()
    try:
        for byte in data:
            # 短い書き込みが返る可能性があるため、少数回リトライして確実に送信する。
            for attempt in range(3):
                written = port.write(bytes([byte]))
                if written == 1:
                    break
                if written in (0, None) and attempt < 2:
                    continue
                raise SerialConnectionError("シリアルへの送信に失敗しました。")
            next_time += frame_seconds
            sleep_seconds = next_time - time.monotonic()
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
        port.flush()
    except (serial.SerialException, OSError, getattr(serial, "SerialTimeoutException", serial.SerialException)) as exc:
        raise SerialConnectionError("シリアルへの送信に失敗しました。") from exc


# シリアルへデータを送信する。
def _send_payload(port: serial.Serial, payload: str, encoding: str, *, encoding_errors: str) -> None:
    """シリアルポートにペイロードを書き込み送信する。"""
    try:
        data = _encode_payload(payload, encoding, errors=encoding_errors)
    except PayloadEncodeError as exc:
        logger.warning("%s", exc)
        return
    try:
        port.write(data)
        port.flush()
    except (serial.SerialException, OSError, getattr(serial, "SerialTimeoutException", serial.SerialException)) as exc:
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


# 重複送信を抑止しながらペイロードを送信する
def _send_payload_with_dedup(
    port: serial.Serial,
    payload: str,
    *,
    state: BufferState,
    send_mode: str,
    encoding: str,
    encoding_errors: str,
    dedup_window_seconds: float,
    serial_config: SerialConfig,
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
    if serial_config.emulate_timing:
        _send_payload_with_timing(
            port,
            payload,
            encoding=encoding,
            encoding_errors=encoding_errors,
            serial_config=serial_config,
        )
    else:
        _send_payload(port, payload, encoding, encoding_errors=encoding_errors)
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
def _open_serial_port(config: AppConfig) -> SerialPortHandle:
    """設定に従ってシリアルポートを開き例外を変換する。"""
    resources: Optional[VirtualPtyResources] = None
    try:
        port_name = config.serial.port
        if port_name == "auto":
            resources = _create_virtual_pty(config.serial)
            port_name = resources.app_slave
        serial_kwargs: dict[str, object] = {
            "port": port_name,
            "baudrate": config.serial.baudrate,
            "timeout": config.serial.timeout,
            "write_timeout": config.serial.write_timeout,
            "bytesize": config.serial.bytesize,
            "parity": config.serial.parity,
            "stopbits": config.serial.stopbits,
            "xonxoff": config.serial.xonxoff,
            "rtscts": config.serial.rtscts,
            "dsrdtr": config.serial.dsrdtr,
        }
        if config.serial.exclusive is not None:
            serial_kwargs["exclusive"] = config.serial.exclusive
        port = serial.Serial(**serial_kwargs)
        try:
            _apply_modem_signal_settings(port, config.serial)
        except SerialConnectionError:
            # モデム制御線の設定失敗時もハンドルを確実に開放する。
            port.close()
            if resources is not None:
                resources.close()
            raise        
        display_port = port_name
        if resources is not None:
            _log_virtual_pty(resources)
            if resources.symlink_path is not None:
                display_port = str(resources.symlink_path)
            else:
                display_port = resources.peer_slave
        return SerialPortHandle(port=port, display_port=display_port, resources=resources)
    except TypeError as exc:
        if resources is not None:
            resources.close()
        raise SerialConnectionError("serial.exclusive は未対応の環境です。") from exc
    except (serial.SerialException, OSError) as exc:
        if resources is not None:
            resources.close()
        reason = str(exc)
        if isinstance(exc, OSError):
            if exc.errno == errno.ENOENT:
                reason = "デバイスが存在しません。"
            elif exc.errno == errno.EACCES:
                reason = "アクセス権限がありません。"
        raise SerialConnectionError(
            f"シリアルポートを開けませんでした: {config.serial.port} ({reason})"
        ) from exc


# 仮想TTYのペアを作成する。
def _create_virtual_pty(serial_config: SerialConfig) -> VirtualPtyResources:
    """仮想シリアルポートを生成してブリッジを起動する。"""
    try:
        master_a, slave_a = pty.openpty()
        master_b, slave_b = pty.openpty()
    except OSError as exc:
        raise SerialConnectionError("仮想TTYの生成に失敗しました。") from exc

    try:
        for fd in (slave_a, slave_b):
            tty.setraw(fd)
        app_slave = os.ttyname(slave_a)
        peer_slave = os.ttyname(slave_b)
    except OSError as exc:
        raise SerialConnectionError("仮想TTYの設定に失敗しました。") from exc
    finally:
        for fd in (slave_a, slave_b):
            try:
                os.close(fd)
            except OSError:
                continue

    try:
        _apply_pty_permissions(app_slave, serial_config)
        _apply_pty_permissions(peer_slave, serial_config)
        symlink_path, created_symlink = _create_pty_symlink(serial_config, peer_slave)
    except SerialConnectionError:
        for fd in (master_a, master_b):
            try:
                os.close(fd)
            except OSError:
                continue
        raise

    bridge = VirtualPtyBridge(master_a, master_b)
    bridge.start()
    return VirtualPtyResources(
        bridge=bridge,
        symlink_path=symlink_path,
        created_symlink=created_symlink,
        app_slave=app_slave,
        peer_slave=peer_slave,
    )


# 仮想TTYのパーミッションを整える。
def _apply_pty_permissions(path: str, serial_config: SerialConfig) -> None:
    """仮想TTYの権限とグループを設定する。"""
    if serial_config.pty_mode is None and serial_config.pty_group is None:
        return
    try:
        if serial_config.pty_mode is not None:
            os.chmod(path, serial_config.pty_mode)
        if serial_config.pty_group is not None:
            gid = grp.getgrnam(serial_config.pty_group).gr_gid
            os.chown(path, -1, gid)
    except KeyError as exc:
        raise SerialConnectionError("serial.pty_group の指定が無効です。") from exc
    except OSError as exc:
        raise SerialConnectionError("仮想TTYの権限設定に失敗しました。") from exc


# 仮想TTYのリンクを作成する。
def _create_pty_symlink(serial_config: SerialConfig, peer_slave: str) -> tuple[Optional[Path], bool]:
    """仮想TTYへのシンボリックリンクを作成する。"""
    if serial_config.pty_link is None:
        return None, False
    link_path = Path(serial_config.pty_link)
    if link_path.exists() or link_path.is_symlink():
        if link_path.is_symlink():
            try:
                link_path.unlink()
            except OSError as exc:
                raise SerialConnectionError("既存のシンボリックリンクを削除できませんでした。") from exc
        else:
            raise SerialConnectionError("serial.pty_link のパスが既に存在しています。")
    try:
        link_path.symlink_to(peer_slave)
    except OSError as exc:
        raise SerialConnectionError("仮想TTYのリンク作成に失敗しました。") from exc
    return link_path, True


# モデム制御線の設定を行う。
def _apply_modem_signal_settings(port: serial.Serial, serial_config: SerialConfig) -> None:
    """指定があればDTR/RTSを明示的に設定する。"""
    dtr = serial_config.dtr
    rts = serial_config.rts
    if serial_config.emulate_modem_signals and dtr is None and rts is None:
        dtr = True
        rts = True
    if dtr is None and rts is None:
        return
    try:
        if dtr is not None:
            port.setDTR(dtr)
        if rts is not None:
            port.setRTS(rts)
    except (serial.SerialException, OSError) as exc:
        raise SerialConnectionError("モデム制御線の設定に失敗しました。") from exc


# 仮想TTY作成時の情報をログ出力する。
def _log_virtual_pty(resources: VirtualPtyResources) -> None:
    """仮想TTYの作成結果をログに記録する。"""
    logger.info("仮想TTYを作成しました (送信側: %s, 受信側: %s)", resources.app_slave, resources.peer_slave)
    if resources.symlink_path is not None:
        logger.info("仮想TTYリンク: %s -> %s", resources.symlink_path, resources.peer_slave)


# 起動時にデバイス情報をログ出力する。
def _log_device_info(device: InputDevice, serial_port: str) -> None:
    """入出力デバイスの情報をログに記録する。"""
    logger.info("入力デバイス: %s", device.path)
    logger.info("シリアル送信先: %s", serial_port)


# 入力デバイスをクローズ
def _close_input_device(device: InputDevice) -> None:
    """入力デバイスをクローズし、失敗時はログに残す。"""
    try:
        device.close()
    except OSError as exc:
        logger.warning("入力デバイスのクローズに失敗しました: %s", exc)


# キーダウン時のバッファ処理と送信判定を行う。
def _handle_key_down(
    keycode: str,
    state: BufferState,
    keymap: KeyMapper,
    line_end: str,
    terminator_keys: Iterable[str],
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
    if keycode in terminator_keys and send_mode == "on_enter":
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
    _reset_buffer(state)
    return payload


# 送信ペイロードがあれば重複抑止付きで送信する
def _send_payload_if_present(
    payload: Optional[str],
    *,
    port: serial.Serial,
    state: BufferState,
    output: OutputConfig,
    serial_config: SerialConfig,
) -> None:
    """送信ペイロードがあれば重複抑止付きで送信する。"""
    if payload is None:
        return
    _send_payload_with_dedup(
        port,
        payload,
        state=state,
        send_mode=output.send_mode,
        encoding=output.encoding,
        encoding_errors=output.encoding_errors,
        dedup_window_seconds=output.dedup_window_seconds,
        serial_config=serial_config,
    )


# キーイベントを解析して必要に応じて送信する。
def _process_key_event(
    event,
    *,
    state: BufferState,
    keymap: KeyMapper,
    output: OutputConfig,
    port: serial.Serial,
    serial_config: SerialConfig,
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
                output.line_end,
                output.terminator_keys,
                output.send_on_enter,
                output.send_mode,
            )
            _send_payload_if_present(
                payload,
                port=port,
                state=state,
                output=output,
                serial_config=serial_config,
            )
    elif key_event.keystate == key_event.key_up:
        for keycode in _iter_keycodes(key_event):
            _handle_key_up(keycode, state)


# アイドルタイムアウト方式のイベントループを実行する。
def _run_event_loop_idle_timeout(config: AppConfig, device: InputDevice, *, keymap: KeyMapper) -> None:
    """一定時間入力が止まったら送信するモードのループ。"""
    state = BufferState()
    output = config.output
    serial_config = config.serial
    with _open_serial_port(config) as port:
        _log_device_info(device, port.display_port)
        while True:
            if state.text and state.last_input_time is not None:
                now = time.monotonic()
                # 入力停止の残り時間を計算して待機時間に使う。
                remaining = output.idle_timeout_seconds - (now - state.last_input_time)
                if remaining <= 0:
                    # タイムアウトを超えたら入力待ちより先に送信して遅延を抑える。
                    payload = _maybe_flush_idle_timeout(
                        state,
                        line_end=output.line_end,
                        idle_timeout_seconds=output.idle_timeout_seconds,
                        now=now,
                    )
                    _send_payload_if_present(
                        payload,
                        port=port,
                        state=state,
                        output=output,
                        serial_config=serial_config,
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
                    line_end=output.line_end,
                    idle_timeout_seconds=output.idle_timeout_seconds,
                    now=time.monotonic(),
                )
                _send_payload_if_present(
                    payload,
                    port=port,
                    state=state,
                    output=output,
                    serial_config=serial_config,
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
                    output=output,
                    port=port,
                    serial_config=serial_config,
                )


# 標準方式のイベントループを実行する。
def _run_event_loop_default(config: AppConfig, device: InputDevice, *, keymap: KeyMapper) -> None:
    """Enter送信/逐次送信向けのイベントループ。"""
    state = BufferState()
    output = config.output
    serial_config = config.serial
    with _open_serial_port(config) as port:
        _log_device_info(device, port.display_port)
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
                    output=output,
                    port=port,
                    serial_config=serial_config,
                )
        except OSError as exc:
            raise DeviceAccessError("入力デバイスの読み取りに失敗しました。") from exc


# 設定に応じて適切なイベントループを起動する。
def run_event_loop(config: AppConfig, *, keymap: KeyMapper = DEFAULT_KEYMAP) -> None:
    """入力モードに応じたイベントループを開始する。"""
    if config.input.mode != "evdev":
        raise ValueError("input.mode は evdev のみサポートしています。")

    _log_available_devices()
    
    while True:
        device: InputDevice | None = None
        try:
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
            return
        except (DeviceNotFoundError, DeviceAccessError, SerialConnectionError) as exc:
            if config.input.reconnect_interval_seconds <= 0:
                raise
            logger.error("%s", exc)
            # Bluetoothの再接続待ちを考慮して一定間隔で再試行する。
            logger.info(
                "再接続を%.1f秒後に試みます。",
                config.input.reconnect_interval_seconds,
            )
            time.sleep(config.input.reconnect_interval_seconds)
        finally:
            if device is not None:
                _close_input_device(device)
