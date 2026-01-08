from __future__ import annotations

import sys
import types

evdev_stub = types.ModuleType("evdev")
evdev_stub.InputDevice = object
evdev_stub.categorize = lambda event: None
evdev_stub.ecodes = types.SimpleNamespace(EV_KEY=1)
evdev_stub.list_devices = lambda: []
sys.modules.setdefault("evdev", evdev_stub)

serial_stub = types.ModuleType("serial")
serial_stub.Serial = object
sys.modules.setdefault("serial", serial_stub)

from key2ser.config import AppConfig, InputConfig, OutputConfig, SerialConfig
from key2ser import runner


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
        ),
        serial=SerialConfig(port="/dev/ttyV0", baudrate=9600, timeout=1.0),
        output=OutputConfig(
            encoding="utf-8",
            line_end="\r\n",
            line_end_mode="literal",
            send_on_enter=True,
            send_mode="on_enter",
            idle_timeout_seconds=0.5,
        ),
    )

    runner._run_event_loop_default(config, DummyDevice(), keymap=runner.DEFAULT_KEYMAP)

    assert dummy_port.writes == [b"a\r\n"]
