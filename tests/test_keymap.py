from key2ser.keymap import DEFAULT_KEYMAP


def test_keymap_maps_letters_with_shift() -> None:
    assert DEFAULT_KEYMAP.map_keycode("KEY_A", shift=False) == "a"
    assert DEFAULT_KEYMAP.map_keycode("KEY_A", shift=True) == "A"


def test_keymap_maps_symbols_with_shift() -> None:
    assert DEFAULT_KEYMAP.map_keycode("KEY_1", shift=False) == "1"
    assert DEFAULT_KEYMAP.map_keycode("KEY_1", shift=True) == "!"


def test_keymap_returns_none_for_unknown_key() -> None:
    assert DEFAULT_KEYMAP.map_keycode("KEY_UNKNOWN", shift=False) is None
