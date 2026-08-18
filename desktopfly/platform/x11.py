"""X11 backend: outputs, window geometry and the global cursor.

This is the fallback that makes the port work on every X11 desktop - i3, bspwm,
XFCE, KDE on X11 - and, through XWayland, on Wayland compositors that do not
implement layer-shell, GNOME above all. Under XWayland the window list only
sees X clients, which is stated in the README compatibility matrix rather than
papered over.

python-xlib is an optional dependency; without it this backend simply reports
itself unavailable.
"""

from __future__ import annotations

from typing import Any

from desktopfly.environment import OutputInfo, WindowRect
from desktopfly.platform.base import OutputSource, PointerSource, WindowSource

OWN_CLASSES = frozenset({"desktop-fly", "desktop-fly-brain"})


def open_display() -> Any | None:
    try:
        from Xlib import display as xdisplay
    except ImportError:
        return None
    try:
        return xdisplay.Display()
    except Exception:
        # Any failure here means there is no usable X server, which is a normal
        # outcome on a pure Wayland session and not something to raise about.
        return None


class X11Base:
    def __init__(self, display: Any):
        self._display = display
        self._root = display.screen().root
        self.available = True

    def _atom(self, name: str) -> int:
        return int(self._display.intern_atom(name))


class X11Outputs(X11Base, OutputSource):
    def outputs(self) -> list[OutputInfo]:
        # Xinerama reports the real multi-head layout; without it the X screen
        # is one big rectangle, which is still a usable answer.
        try:
            if self._display.has_extension("XINERAMA"):
                screens = self._display.xinerama_query_screens().screens
                return [
                    OutputInfo(
                        name=f"screen-{index}",
                        x=int(screen.x),
                        y=int(screen.y),
                        width=int(screen.width),
                        height=int(screen.height),
                    )
                    for index, screen in enumerate(screens)
                ]
        except Exception:
            pass
        screen = self._display.screen()
        return [
            OutputInfo(
                name="screen-0",
                x=0,
                y=0,
                width=int(screen.width_in_pixels),
                height=int(screen.height_in_pixels),
            )
        ]

    def current(self) -> OutputInfo | None:
        outputs = self.outputs()
        if not outputs:
            return None
        pointer = X11Pointer(self._display).position()
        if pointer is None:
            return outputs[0]
        x, y = pointer
        for output in outputs:
            if output.x <= x < output.x + output.width and output.y <= y < output.y + output.height:
                return output
        return outputs[0]

    def describe(self) -> str:
        names = ", ".join(f"{o.name} {o.width}x{o.height}" for o in self.outputs())
        return f"outputs: X11 ({names})"


class X11Windows(X11Base, WindowSource):
    def rects(self) -> list[WindowRect]:
        try:
            client_list = self._root.get_full_property(self._atom("_NET_CLIENT_LIST"), 0)
        except Exception:
            return []
        if client_list is None:
            return []

        hidden_atom = self._atom("_NET_WM_STATE_HIDDEN")
        state_atom = self._atom("_NET_WM_STATE")
        rects = []
        for window_id in client_list.value:
            try:
                window = self._display.create_resource_object("window", window_id)
                state = window.get_full_property(state_atom, 0)
                if state is not None and hidden_atom in state.value:
                    continue
                instance_class = window.get_wm_class()
                if instance_class and set(instance_class) & OWN_CLASSES:
                    continue
                geometry = window.get_geometry()
                # A window's own geometry is relative to its parent, so translate
                # through the root to get the position the user actually sees.
                origin = window.translate_coords(self._root, 0, 0)
                rects.append(
                    WindowRect(
                        key=int(window_id),
                        x=int(-origin.x),
                        y=int(-origin.y),
                        width=int(geometry.width),
                        height=int(geometry.height),
                    )
                )
            except Exception:
                continue  # the window vanished between the list and the query
        return rects

    def describe(self) -> str:
        return f"windows: X11 _NET_CLIENT_LIST ({len(self.rects())} visible)"


class X11Pointer(X11Base, PointerSource):
    def position(self) -> tuple[float, float] | None:
        try:
            pointer = self._root.query_pointer()
        except Exception:
            return None
        return (float(pointer.root_x), float(pointer.root_y))

    def describe(self) -> str:
        return f"pointer: X11 XQueryPointer (at {self.position()})"
