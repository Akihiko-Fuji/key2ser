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
