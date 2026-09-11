"""Choose the best backend set for the running session, and say what was lost.

The rule is that a missing sense degrades and the app keeps running. Anything
this function could not find turns into a named Null source, so --probe reports
the gap instead of the program failing at the first poll.
"""

from __future__ import annotations

import os

from desktopfly.platform import hyprland, sway, x11
from desktopfly.platform.base import (
    Backends,
    NullPointerSource,
    NullTapSource,
    NullThermalSource,
    NullWindowSource,
    OutputInfo,
    OutputSource,
    PointerSource,
    TapSource,
    ThermalSource,
    WindowSource,
)
from desktopfly.platform.linux_input import EvdevActivity
from desktopfly.platform.linux_thermal import SysfsThermal


class _NoOutputs(OutputSource):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def outputs(self) -> list[OutputInfo]:
        return []

    def current(self) -> None:
        return None

    def describe(self) -> str:
        return f"outputs: unavailable ({self.reason})"


def detect(taps_enabled: bool = True) -> Backends:
    name = "unknown"
    outputs: OutputSource = _NoOutputs("no compositor recognised")
    windows: WindowSource = NullWindowSource("no compositor recognised")
    pointer: PointerSource = NullPointerSource("no compositor recognised")

    hypr_socket = hyprland.instance_socket()
    sway_socket = sway.socket_path()

    if hypr_socket is not None:
        name = "hyprland (wayland)"
        hypr_ipc = hyprland.HyprlandIPC(hypr_socket)
        outputs = hyprland.HyprlandOutputs(hypr_ipc)
        windows = hyprland.HyprlandWindows(hypr_ipc)
        pointer = hyprland.HyprlandPointer(hypr_ipc)
    elif sway_socket is not None:
        name = "sway/i3"
        sway_ipc = sway.SwayIPC(sway_socket)
        outputs = sway.SwayOutputs(sway_ipc)
        windows = sway.SwayWindows(sway_ipc)
        # No Wayland protocol exposes the pointer position and sway has no IPC
        # command for it, so the fly runs blind here. See DESIGN.md section 4.3.
        pointer = NullPointerSource("sway exposes no cursor position")

    if not windows.available or not pointer.available:
        # X11 fills whichever gaps are left, and is also the whole answer on a
        # plain X session or under XWayland.
        display = x11.open_display()
        if display is not None:
            if name == "unknown":
                name = "x11" if not os.environ.get("WAYLAND_DISPLAY") else "xwayland"
                outputs = x11.X11Outputs(display)
            if not windows.available:
                windows = x11.X11Windows(display)
            if not pointer.available:
                pointer = x11.X11Pointer(display)

    taps: TapSource = NullTapSource("disabled in the configuration")
    if taps_enabled:
        evdev = EvdevActivity()
        taps = evdev if evdev.available else NullTapSource("/dev/input not readable")

    thermal: ThermalSource = SysfsThermal()
    if not thermal.available:
        thermal = NullThermalSource("no CPU sensor in sysfs")

    return Backends(name, outputs, windows, pointer, taps, thermal)
