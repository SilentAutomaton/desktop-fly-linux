"""Hyprland backend: window geometry, outputs and the global cursor.

Hyprland's IPC is a unix socket that answers plain text commands. It is used
directly rather than through the hyprctl binary, so polling the cursor 30 times
a second costs a socket round trip instead of a process spawn.

This is the one Wayland compositor in this port that will tell a client where
the pointer is, which is why it is the reference target: with it the fly sees a
cursor lunge as a real looming stimulus, exactly as on macOS.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

from desktopfly.environment import OutputInfo, WindowRect
from desktopfly.platform.base import OutputSource, PointerSource, WindowSource

# The fly's own surfaces, excluded from the window list so it cannot land on
# itself or be startled by its own brain window opening.
OWN_CLASSES = frozenset({"desktop-fly", "desktop-fly-brain"})


def instance_socket() -> Path | None:
    """Locate the running compositor's IPC socket.

    Nothing is hardcoded past the two documented locations: Hyprland moved the
    socket from /tmp to $XDG_RUNTIME_DIR, and both are still in the wild.
    """
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not signature:
        return None
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    candidates = []
    if runtime:
        candidates.append(Path(runtime) / "hypr" / signature / ".socket.sock")
    candidates.append(Path("/tmp/hypr") / signature / ".socket.sock")
    return next((path for path in candidates if path.exists()), None)


class HyprlandIPC:
    """One socket connection per request, which is what the protocol expects."""

    def __init__(self, path: Path):
        self.path = path

    def ask(self, command: str) -> str:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(str(self.path))
            client.sendall(command.encode())
            chunks = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")

    def ask_json(self, command: str) -> Any:
        return json.loads(self.ask(f"j/{command}"))


class HyprlandOutputs(OutputSource):
    def __init__(self, ipc: HyprlandIPC):
        self._ipc = ipc
        self.available = True

    def outputs(self) -> list[OutputInfo]:
        try:
            monitors = self._ipc.ask_json("monitors")
        except (OSError, ValueError):
            return []
        return [
            OutputInfo(
                name=monitor["name"],
                x=int(monitor["x"]),
                y=int(monitor["y"]),
                width=int(monitor["width"] / monitor.get("scale", 1.0)),
                height=int(monitor["height"] / monitor.get("scale", 1.0)),
                scale=float(monitor.get("scale", 1.0)),
            )
            for monitor in monitors
        ]

    def current(self) -> OutputInfo | None:
        try:
            monitors = self._ipc.ask_json("monitors")
        except (OSError, ValueError):
            return None
        focused = next((m for m in monitors if m.get("focused")), None) or (
            monitors[0] if monitors else None
        )
        if focused is None:
            return None
        return next((o for o in self.outputs() if o.name == focused["name"]), None)

    def describe(self) -> str:
        names = ", ".join(f"{o.name} {o.width}x{o.height}" for o in self.outputs())
        return f"outputs: hyprland ({names or 'none'})"


class HyprlandWindows(WindowSource):
    def __init__(self, ipc: HyprlandIPC):
        self._ipc = ipc
        self.available = True

    def rects(self) -> list[WindowRect]:
        try:
            clients = self._ipc.ask_json("clients")
            monitors = self._ipc.ask_json("monitors")
        except (OSError, ValueError):
            return []
        # Only what is actually on screen: a window on another workspace is not
        # terrain, however real it is to the compositor.
        visible_workspaces = {m["activeWorkspace"]["id"] for m in monitors}
        rects = []
        for client in clients:
            if not client.get("mapped") or client.get("hidden"):
                continue
            if client["workspace"]["id"] not in visible_workspaces:
                continue
            if client.get("class") in OWN_CLASSES:
                continue
            x, y = client["at"]
            width, height = client["size"]
            monitor_index = client.get("monitor", 0)
            monitor_name = next((m["name"] for m in monitors if m.get("id") == monitor_index), "")
            rects.append(
                WindowRect(
                    # The address is a hex string and is stable while the window
                    # lives, which is exactly what the new-window diff needs.
                    key=int(str(client["address"]), 16),
                    x=int(x),
                    y=int(y),
                    width=int(width),
                    height=int(height),
                    output=monitor_name,
                )
            )
        return rects

    def describe(self) -> str:
        return f"windows: hyprland IPC ({len(self.rects())} visible)"


class HyprlandPointer(PointerSource):
    def __init__(self, ipc: HyprlandIPC):
        self._ipc = ipc
        self.available = True

    def position(self) -> tuple[float, float] | None:
        try:
            point = self._ipc.ask_json("cursorpos")
        except (OSError, ValueError):
            return None
        return (float(point["x"]), float(point["y"]))

    def describe(self) -> str:
        return f"pointer: hyprland IPC (at {self.position()})"
