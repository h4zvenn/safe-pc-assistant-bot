"""Push-to-talk: reads the state of ONE configured key (or key combination).

Uses GetAsyncKeyState polling instead of a global keyboard hook, so no other key presses
are ever observed or logged.
"""

from __future__ import annotations

import ctypes
import time

_VK: dict[str, int] = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "space": 0x20, "tab": 0x09,
    "caps": 0x14, "capslock": 0x14, "scrolllock": 0x91, "scroll": 0x91, "pause": 0x13,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "mouse4": 0x05, "mouse5": 0x06, "xbutton1": 0x05, "xbutton2": 0x06,
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63, "numpad4": 0x64,
    "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67, "numpad8": 0x68, "numpad9": 0x69,
}
_VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})  # F1 = 0x70
_VK.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})

# Letters: the virtual-key code is the upper-case character code.
_LETTER_FIX = {chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)}


def parse_key(spec: str) -> list[int]:
    """'F9' -> [0x78]; 'ctrl+space' -> [0x11, 0x20]. Raises ValueError for unknown names."""
    codes: list[int] = []
    for part in spec.lower().replace(" ", "").split("+"):
        if part in _LETTER_FIX:
            codes.append(_LETTER_FIX[part])
        elif part in _VK:
            codes.append(_VK[part])
        else:
            raise ValueError(
                f"Unknown push-to-talk key {part!r}. Examples: F9, scrolllock, ctrl+space, mouse4."
            )
    if not codes:
        raise ValueError("Push-to-talk key is empty.")
    return codes


class PushToTalk:
    def __init__(self, spec: str) -> None:
        self.spec = spec
        self._codes = parse_key(spec)
        self._get = ctypes.windll.user32.GetAsyncKeyState

    def is_down(self) -> bool:
        return all(self._get(code) & 0x8000 for code in self._codes)

    def wait_for_press(self, poll_seconds: float = 0.02) -> None:
        # Wait for release first so a key held at startup does not start a recording.
        while self.is_down():
            time.sleep(poll_seconds)
        while not self.is_down():
            time.sleep(poll_seconds)

    def wait_for_release(self, max_seconds: float, poll_seconds: float = 0.02) -> None:
        deadline = time.monotonic() + max_seconds
        while self.is_down() and time.monotonic() < deadline:
            time.sleep(poll_seconds)
