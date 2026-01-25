from __future__ import annotations

from dataclasses import replace
import errno
import sys
import types

import pytest

evdev_stub = types.ModuleType("evdev")
evdev_stub.InputDevice = object
evdev_stub.categorize = lambda event: None
evdev_stub.ecodes = types.SimpleNamespace(EV_KEY=1)
evdev_stub.list_devices = lambda: []
sys.modules.setdefault("evdev", evdev_stub)

serial_stub = types.ModuleType("serial")
serial_stub.Serial = object
serial_stub.SerialException = type("SerialException", (Exception,), {})
sys.modules.setdefault("serial", serial_stub)

from key2ser.config import AppConfig, InputConfig, OutputConfig, SerialConfig
from key2ser import runner


def _default_serial_config() -> SerialConfig:
    return SerialConfig(
        port="/dev/ttyV0",
        baudrate=9600,
        timeout=1.0,
        bytesize=8,
        parity="N",
        stopbits=1.0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        emulate_modem_signals=False,
        emulate_timing=False,
    )


def test_runner_per_char_sends_immediately() -> None:
    state = runner.BufferState()

    payload = runner._handle_key_down(
        "KEY_A",
        state,
        runner.DEFAULT_KEYMAP,
        "\r\n",
        True,
        "per_char",
    )

    assert payload == "a"
    assert state.text == ""
    assert state.last_input_time is None


def test_runner_idle_timeout_sends_after_wait(monkeypatch) -> None:
    state = runner.BufferState()
    monkeypatch.setattr(runner.time, "monotonic", lambda: 10.0)

    payload = runner._handle_key_down(
        "KEY_A",
        state,
        runner.DEFAULT_KEYMAP,
        "\r\n",
        True,
        "idle_timeout",
    )

    assert payload is None
    assert state.text == "a"
    assert state.last_input_time == 10.0

    assert (
        runner._maybe_flush_idle_timeout(
            state,
            line_end="\r\n",
            idle_timeout_seconds=0.5,
            now=10.4,
        )
        is None
    )

    payload = runner._maybe_flush_idle_timeout(
        state,
        line_end="\r\n",
        idle_timeout_seconds=0.5,
        now=10.6,
    )
    assert payload == "a\r\n"
    assert state.text == ""
    assert state.last_input_time is None


def test_runner_on_enter_unchanged() -> None:
    state = runner.BufferState()

    payload = runner._handle_key_down(
        "KEY_A",
        state,
        runner.DEFAULT_KEYMAP,
        "\r\n",
        True,
        "on_enter",
    )

    assert payload is None
    assert state.text == "a"

    payload = runner._handle_key_down(
        "KEY_ENTER",
        state,
        runner.DEFAULT_KEYMAP,
        "\r\n",
        True,
        "on_enter",
    )

    assert payload == "a\r\n"


def test_open_input_device_handles_permission_error(monkeypatch, tmp_path) -> None:
    device_path = tmp_path / "event0"
    device_path.write_text("dummy")

    def raise_permission_error(_path: str):
        raise PermissionError("no permission")

    monkeypatch.setattr(runner, "InputDevice", raise_permission_error)

    config = InputConfig(
        mode="evdev",
        device=str(device_path),
        vendor_id=None,
        product_id=None,
        grab=False,
        reconnect_interval_seconds=0,
    )

    with pytest.raises(runner.DeviceAccessError, match="入力デバイスへのアクセス権限がありません。"):
        runner.open_input_device(config)


def test_open_input_device_handles_list_error(monkeypatch) -> None:
    def raise_list_error():
        raise OSError("list error")

    monkeypatch.setattr(runner, "list_devices", raise_list_error)

    config = InputConfig(
        mode="evdev",
        device=None,
        vendor_id=0x1234,
        product_id=0x5678,
        grab=False,
        reconnect_interval_seconds=0,
    )

    with pytest.raises(runner.DeviceAccessError, match="入力デバイス一覧の取得に失敗しました。"):
        runner.open_input_device(config)


def test_open_input_device_handles_device_open_error(monkeypatch) -> None:
    def raise_device_error(_path: str):
        raise OSError("device error")

    monkeypatch.setattr(runner, "InputDevice", raise_device_error)
    monkeypatch.setattr(runner, "list_devices", lambda: ["/dev/input/event0"])

    config = InputConfig(
        mode="evdev",
        device=None,
        vendor_id=0x1234,
        product_id=0x5678,
        grab=False,
        reconnect_interval_seconds=0,
    )

    with pytest.raises(runner.DeviceAccessError, match="入力デバイスのオープンに失敗しました。"):
        runner.open_input_device(config)


def test_open_serial_port_handles_serial_exception(monkeypatch) -> None:
    def raise_serial_error(**_kwargs):
        raise serial_stub.SerialException("serial error")

    monkeypatch.setattr(runner.serial, "Serial", raise_serial_error)

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=0,
        ),
        serial=_default_serial_config(),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    with pytest.raises(
        runner.SerialConnectionError,
        match=r"シリアルポートを開けませんでした: /dev/ttyV0 \(serial error\)",
    ):
        runner._open_serial_port(config)


def test_open_serial_port_handles_missing_device(monkeypatch) -> None:
    def raise_os_error(**_kwargs):
        raise OSError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr(runner.serial, "Serial", raise_os_error)

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=0,
        ),
        serial=_default_serial_config(),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    with pytest.raises(
        runner.SerialConnectionError,
        match=r"シリアルポートを開けませんでした: /dev/ttyV0 \(デバイスが存在しません。\)",
    ):
        runner._open_serial_port(config)


def test_open_serial_port_passes_serial_settings(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyPort:
        pass

    def fake_serial(**kwargs):
        captured.update(kwargs)
        return DummyPort()

    monkeypatch.setattr(runner.serial, "Serial", fake_serial)

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=0,
        ),
        serial=replace(
            _default_serial_config(),
            bytesize=7,
            parity="E",
            stopbits=2.0,
            xonxoff=True,
            rtscts=True,
            dsrdtr=True,
        ),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    runner._open_serial_port(config)

    assert captured["bytesize"] == 7
    assert captured["parity"] == "E"
    assert captured["stopbits"] == 2.0
    assert captured["xonxoff"] is True
    assert captured["rtscts"] is True
    assert captured["dsrdtr"] is True


def test_open_serial_port_emulates_modem_signals(monkeypatch) -> None:
    class DummyPort:
        def __init__(self) -> None:
            self.dtr: bool | None = None
            self.rts: bool | None = None

        def setDTR(self, value: bool) -> None:
            self.dtr = value

        def setRTS(self, value: bool) -> None:
            self.rts = value

    dummy_port = DummyPort()

    monkeypatch.setattr(runner.serial, "Serial", lambda **_kwargs: dummy_port)

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=0,
        ),
        serial=replace(_default_serial_config(), emulate_modem_signals=True),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    port = runner._open_serial_port(config)

    assert port.dtr is True
    assert port.rts is True


def test_send_payload_handles_serial_error() -> None:
    class DummyPort:
        def write(self, _data: bytes) -> None:
            raise OSError("write failed")

        def flush(self) -> None:
            return None

    with pytest.raises(runner.SerialConnectionError, match="シリアルへの送信に失敗しました。"):
        runner._send_payload(DummyPort(), "a", "utf-8")


def test_send_payload_with_dedup_suppresses_duplicate(monkeypatch) -> None:
    class DummyPort:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        def flush(self) -> None:
            return None

    state = runner.BufferState()
    port = DummyPort()

    times = iter([10.0, 10.1])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(times))

    runner._send_payload_with_dedup(
        port,
        "payload",
        state=state,
        send_mode="on_enter",
        encoding="utf-8",
        dedup_window_seconds=0.2,
        serial_config=_default_serial_config(),
    )
    runner._send_payload_with_dedup(
        port,
        "payload",
        state=state,
        send_mode="on_enter",
        encoding="utf-8",
        dedup_window_seconds=0.2,
        serial_config=_default_serial_config(),
    )

    assert port.writes == [b"payload"]


def test_send_payload_with_dedup_allows_after_window(monkeypatch) -> None:
    class DummyPort:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        def flush(self) -> None:
            return None

    state = runner.BufferState()
    port = DummyPort()

    times = iter([10.0, 10.5])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(times))

    runner._send_payload_with_dedup(
        port,
        "payload",
        state=state,
        send_mode="on_enter",
        encoding="utf-8",
        dedup_window_seconds=0.2,
        serial_config=_default_serial_config(),
    )
    runner._send_payload_with_dedup(
        port,
        "payload",
        state=state,
        send_mode="on_enter",
        encoding="utf-8",
        dedup_window_seconds=0.2,
        serial_config=_default_serial_config(),
    )

    assert port.writes == [b"payload", b"payload"]


def test_send_payload_with_dedup_does_not_block_per_char(monkeypatch) -> None:
    class DummyPort:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        def flush(self) -> None:
            return None

    state = runner.BufferState()
    port = DummyPort()

    times = iter([10.0, 10.1])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(times))

    runner._send_payload_with_dedup(
        port,
        "a",
        state=state,
        send_mode="per_char",
        encoding="utf-8",
        dedup_window_seconds=0.2,
        serial_config=_default_serial_config(),
    )
    runner._send_payload_with_dedup(
        port,
        "a",
        state=state,
        send_mode="per_char",
        encoding="utf-8",
        dedup_window_seconds=0.2,
        serial_config=_default_serial_config(),
    )

    assert port.writes == [b"a", b"a"]


def test_send_payload_with_timing_emulation(monkeypatch) -> None:
    class DummyPort:
        def __init__(self) -> None:
            self.writes: list[bytes] = []
            self.flushed = False

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        def flush(self) -> None:
            self.flushed = True

    port = DummyPort()
    state = runner.BufferState()

    monkeypatch.setattr(runner.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    runner._send_payload_with_dedup(
        port,
        "ab",
        state=state,
        send_mode="on_enter",
        encoding="utf-8",
        dedup_window_seconds=0.0,
        serial_config=replace(_default_serial_config(), emulate_timing=True),
    )

    assert port.writes == [b"a", b"b"]
    assert port.flushed is True


def test_encode_payload_handles_invalid_encoding() -> None:
    with pytest.raises(ValueError, match="output.encoding に未対応の文字コードが指定されています。"):
        runner._encode_payload("a", "invalid-encoding")


def test_run_event_loop_default_handles_read_loop_error(monkeypatch) -> None:
    class DummyPort:
        def __enter__(self) -> "DummyPort":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class DummyDevice:
        path = "/dev/input/event0"

        def read_loop(self):
            raise OSError("read error")

    monkeypatch.setattr(runner, "_open_serial_port", lambda config: DummyPort())
    monkeypatch.setattr(runner, "_log_device_info", lambda device, config: None)

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=0,
        ),
        serial=_default_serial_config(),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    with pytest.raises(runner.DeviceAccessError, match="入力デバイスの監視を開始できませんでした。"):
        runner._run_event_loop_default(config, DummyDevice(), keymap=runner.DEFAULT_KEYMAP)


def test_run_event_loop_default_handles_read_error(monkeypatch) -> None:
    class DummyPort:
        def __enter__(self) -> "DummyPort":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class DummyDevice:
        path = "/dev/input/event0"

        def read_loop(self):
            yield "event"
            raise OSError("read error")

    monkeypatch.setattr(runner, "_open_serial_port", lambda config: DummyPort())
    monkeypatch.setattr(runner, "_log_device_info", lambda device, config: None)
    monkeypatch.setattr(runner, "_process_key_event", lambda *args, **kwargs: None)

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=0,
        ),
        serial=_default_serial_config(),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    with pytest.raises(runner.DeviceAccessError, match="入力デバイスの読み取りに失敗しました。"):
        runner._run_event_loop_default(config, DummyDevice(), keymap=runner.DEFAULT_KEYMAP)


def test_run_event_loop_default_sends_on_enter(monkeypatch) -> None:
    class DummyPort:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        def flush(self) -> None:
            return None

        def __enter__(self) -> "DummyPort":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class DummyKeyEvent:
        key_down = 1
        key_up = 0

        def __init__(self, keycode: str, keystate: int) -> None:
            self.keycode = keycode
            self.keystate = keystate

    class DummyEvent:
        def __init__(self, keycode: str, keystate: int) -> None:
            self.type = runner.ecodes.EV_KEY
            self.keycode = keycode
            self.keystate = keystate

    class DummyDevice:
        path = "/dev/input/event0"

        def read_loop(self):
            yield DummyEvent("KEY_A", DummyKeyEvent.key_down)
            yield DummyEvent("KEY_ENTER", DummyKeyEvent.key_down)

    dummy_port = DummyPort()

    monkeypatch.setattr(runner, "_open_serial_port", lambda config: dummy_port)
    monkeypatch.setattr(runner, "_log_device_info", lambda device, config: None)
    monkeypatch.setattr(runner, "categorize", lambda event: DummyKeyEvent(event.keycode, event.keystate))

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=0,
        ),
        serial=_default_serial_config(),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    runner._run_event_loop_default(config, DummyDevice(), keymap=runner.DEFAULT_KEYMAP)

    assert dummy_port.writes == [b"a\r\n"]


def test_run_event_loop_retries_on_serial_error(monkeypatch) -> None:
    class DummyDevice:
        path = "/dev/input/event0"

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    dummy_device = DummyDevice()
    sleeps: list[float] = []
    calls = {"count": 0}

    def fake_run_event_loop_default(_config, _device, *, keymap):
        calls["count"] += 1
        if calls["count"] == 1:
            raise runner.SerialConnectionError("serial down")
        raise RuntimeError("stop")

    monkeypatch.setattr(runner, "open_input_device", lambda _config: dummy_device)
    monkeypatch.setattr(runner, "_run_event_loop_default", fake_run_event_loop_default)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: sleeps.append(seconds))

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=1.5,
        ),
        serial=_default_serial_config(),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    with pytest.raises(RuntimeError, match="stop"):
        runner.run_event_loop(config)

    assert sleeps == [1.5]
    assert dummy_device.closed is True


def test_run_event_loop_raises_when_reconnect_disabled(monkeypatch) -> None:
    def raise_not_found(_config):
        raise runner.DeviceNotFoundError("not found")

    monkeypatch.setattr(runner, "open_input_device", raise_not_found)

    config = AppConfig(
        input=InputConfig(
            mode="evdev",
            device="/dev/input/event0",
            vendor_id=None,
            product_id=None,
            grab=False,
            reconnect_interval_seconds=0,
        ),
        serial=_default_serial_config(),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
            dedup_window_seconds=0.2,
        ),
    )

    with pytest.raises(runner.DeviceNotFoundError, match="not found"):
        runner.run_event_loop(config)
