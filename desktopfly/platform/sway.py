"""sway and i3 backend: window geometry and outputs over the i3 IPC protocol.

river and Wayfire are covered by the same layer-shell overlay but do not speak
this protocol, so on those the window list simply stays empty and the fly walks
the wallpaper. No compositor in this family exposes the pointer position, so
there is no PointerSource here: see DESIGN.md section 4.3.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
from typing import Any

from desktopfly.environment import OutputInfo, WindowRect
from desktopfly.platform.base import OutputSource, WindowSource

MAGIC = b"i3-ipc"
GET_OUTPUTS = 3
GET_TREE = 4

OWN_APP_IDS = frozenset({"desktop-fly", "desktop-fly-brain"})


def socket_path() -> str | None:
    path = os.environ.get("SWAYSOCK") or os.environ.get("I3SOCK")
    if path:
        return path
    # i3 does not always export the variable, but it will always print it.
    for binary in ("i3", "sway"):
        if shutil.which(binary):
            try:
                found = subprocess.run(
                    [binary, "--get-socketpath"], capture_output=True, text=True, timeout=1
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if found.returncode == 0 and found.stdout.strip():
                return found.stdout.strip()
    return None


class SwayIPC:
    def __init__(self, path: str):
        self.path = path

    def ask(self, message_type: int) -> Any:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(self.path)
            client.sendall(MAGIC + struct.pack("=II", 0, message_type))
            header = self._recv_exactly(client, len(MAGIC) + 8)
            length, _ = struct.unpack("=II", header[len(MAGIC) :])
            payload = self._recv_exactly(client, length)
        return json.loads(payload)

    @staticmethod
    def _recv_exactly(client: socket.socket, count: int) -> bytes:
        chunks = []
        remaining = count
        while remaining > 0:
            chunk = client.recv(remaining)
            if not chunk:
                raise OSError("i3 IPC: connection closed mid-message")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class SwayOutputs(OutputSource):
    def __init__(self, ipc: SwayIPC):
        self._ipc = ipc
        self.available = True

    def outputs(self) -> list[OutputInfo]:
        try:
            outputs = self._ipc.ask(GET_OUTPUTS)
        except (OSError, ValueError):
            return []
        return [
            OutputInfo(
                name=output["name"],
                x=int(output["rect"]["x"]),
                y=int(output["rect"]["y"]),
                width=int(output["rect"]["width"]),
                height=int(output["rect"]["height"]),
                scale=float(output.get("scale", 1.0)),
            )
            for output in outputs
            if output.get("active", True)
        ]

    def current(self) -> OutputInfo | None:
        try:
            outputs = self._ipc.ask(GET_OUTPUTS)
        except (OSError, ValueError):
            return None
        focused = next((o for o in outputs if o.get("focused")), None)
        known = self.outputs()
        if focused is not None:
            return next((o for o in known if o.name == focused["name"]), None)
        return known[0] if known else None

    def describe(self) -> str:
        names = ", ".join(f"{o.name} {o.width}x{o.height}" for o in self.outputs())
        return f"outputs: sway/i3 IPC ({names or 'none'})"


class SwayWindows(WindowSource):
    def __init__(self, ipc: SwayIPC):
        self._ipc = ipc
        self.available = True

    def rects(self) -> list[WindowRect]:
        try:
            tree = self._ipc.ask(GET_TREE)
        except (OSError, ValueError):
            return []
        rects: list[WindowRect] = []
        self._collect(tree, "", rects)
        return rects

    def _collect(self, node: dict[str, Any], output: str, rects: list[WindowRect]) -> None:
        if node.get("type") == "output":
            output = node.get("name", output)
        # A leaf with a window is a real window; containers only carry layout.
        is_window = node.get("pid") is not None or node.get("window") is not None
        identifier = node.get("app_id") or (node.get("window_properties") or {}).get("class")
        if is_window and identifier not in OWN_APP_IDS and node.get("visible") is not False:
            rect = node.get("rect", {})
            if rect.get("width") and rect.get("height"):
                rects.append(
                    WindowRect(
                        key=int(node["id"]),
                        x=int(rect["x"]),
                        y=int(rect["y"]),
                        width=int(rect["width"]),
                        height=int(rect["height"]),
                        output=output,
                    )
                )
        for child in list(node.get("nodes", [])) + list(node.get("floating_nodes", [])):
            self._collect(child, output, rects)

    def describe(self) -> str:
        return f"windows: sway/i3 IPC ({len(self.rects())} visible)"
