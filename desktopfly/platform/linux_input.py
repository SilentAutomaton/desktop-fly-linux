"""Coarse input activity from /dev/input, opt-in and deliberately blind.

Upstream gets substrate taps from a global NSEvent monitor and vibration from
CGEventSource's idle query - permission-free on macOS, impossible on Wayland by
design. The nearest honest equivalent on Linux is the evdev stream, so this
module reads it and throws almost all of it away: for every event it increments
one of two counters, "a pointer button went down" or "a key went down", and
records when. The key code is never stored, never logged and never leaves the
read loop. That is strictly less information than upstream's macOS query.

It needs read access to /dev/input/event*, which on most distributions means
membership of the `input` group. Without it the source reports unavailable and
the app derives its activity signal from the cursor and the window list
instead; nothing crashes and the fly still works.
"""

from __future__ import annotations

import os
import select
import struct
import threading
import time
from pathlib import Path

from desktopfly.environment import Activity
from desktopfly.platform.base import TapSource

INPUT_ROOT = Path("/dev/input")
DEVICE_LIST = Path("/proc/bus/input/devices")

# struct input_event: struct timeval, __u16 type, __u16 code, __s32 value
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

EV_KEY = 0x01
KEY_PRESS = 1  # 0 is release and 2 is auto-repeat; only a real press counts

# Pointer and other buttons live in this code range; everything else is a key.
BUTTON_RANGE = range(0x100, 0x200)


def keyboard_and_pointer_devices() -> list[Path]:
    """Devices that emit EV_KEY, read from the kernel's own listing.

    /proc/bus/input/devices is parsed rather than probing with ioctls because it
    already states both the handler name and the supported event bitmask.
    """
    try:
        blocks = DEVICE_LIST.read_text().split("\n\n")
    except OSError:
        return []
    devices = []
    for block in blocks:
        handlers = ""
        supported = 0
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1]
            elif line.startswith("B: EV="):
                supported = int(line.split("=", 1)[1].strip(), 16)
        if not supported & (1 << EV_KEY):
            continue
        for token in handlers.split():
            if token.startswith("event"):
                devices.append(INPUT_ROOT / token)
    return devices


class EvdevActivity(TapSource):
    """A daemon thread that blocks on the devices and only counts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._taps = 0
        self._keys = 0
        self._last_event = 0.0
        self._descriptors: list[int] = []
        self._names: list[str] = []
        self._stop = threading.Event()

        for path in keyboard_and_pointer_devices():
            try:
                self._descriptors.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
                self._names.append(path.name)
            except OSError:
                continue  # no permission for this one; others may still work
        self.available = bool(self._descriptors)
        if self.available:
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            readable, _, _ = select.select(self._descriptors, [], [], 0.5)
            for descriptor in readable:
                try:
                    payload = os.read(descriptor, EVENT_SIZE * 64)
                except OSError:
                    continue
                self._count(payload)

    def _count(self, payload: bytes) -> None:
        taps = keys = 0
        for offset in range(0, len(payload) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, event_type, code, value = struct.unpack_from(EVENT_FORMAT, payload, offset)
            if event_type != EV_KEY or value != KEY_PRESS:
                continue
            if code in BUTTON_RANGE:
                taps += 1
            else:
                keys += 1
        if taps or keys:
            with self._lock:
                self._taps += taps
                self._keys += keys
                self._last_event = time.monotonic()

    def drain(self) -> Activity:
        with self._lock:
            taps, keys, last = self._taps, self._keys, self._last_event
            self._taps = self._keys = 0
        since = float("inf") if last == 0.0 else time.monotonic() - last
        return Activity(taps=taps, keys=keys, seconds_since_event=since)

    def close(self) -> None:
        self._stop.set()
        for descriptor in self._descriptors:
            os.close(descriptor)
        self._descriptors = []

    def describe(self) -> str:
        if not self.available:
            return "taps: /dev/input not readable (add the user to the `input` group)"
        return f"taps: /dev/input, {len(self._descriptors)} devices ({', '.join(self._names)})"
