"""The click-through overlay the fly lives on.

Replaces upstream's borderless NSWindow. Two paths behind one class:

Wayland uses wlr-layer-shell. A layer surface is not part of any tiling layout
and sits above normal windows by protocol, so the same code gives the same
result under a tiling compositor and a floating one - which is the whole point
of DESIGN.md section 1.2.

X11 uses an override-redirect dock window kept above and sticky, which is the
same idea one protocol down, and also serves as the XWayland fallback for
compositors without layer-shell.

Either way the surface takes an empty input region, so every click, scroll and
gesture passes straight through to whatever is underneath.
"""

from __future__ import annotations

from collections.abc import Callable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from desktopfly.environment import OutputInfo  # noqa: E402
from desktopfly.platform.base import Overlay  # noqa: E402

APPLICATION_ID = "desktop-fly"

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell

    LAYER_SHELL_AVAILABLE = True
except (ImportError, ValueError):
    GtkLayerShell = None  # type: ignore[assignment]
    LAYER_SHELL_AVAILABLE = False


def _gdk_monitor_for(output: OutputInfo) -> Gdk.Monitor | None:
    """Match an OutputInfo back to the GdkMonitor the toolkit knows about."""
    display = Gdk.Display.get_default()
    if display is None:
        return None
    for index in range(display.get_n_monitors()):
        monitor = display.get_monitor(index)
        if monitor.get_model() == output.name:
            return monitor
        geometry = monitor.get_geometry()
        if geometry.x == output.x and geometry.y == output.y:
            return monitor
    return display.get_primary_monitor() or display.get_monitor(0)


class GtkOverlay(Overlay):
    def __init__(self, on_render: Callable[[], None], on_realize: Callable[[], None]) -> None:
        self._on_render = on_render
        self._on_realize = on_realize
        self._on_frame: Callable[[float], None] | None = None
        self.window: Gtk.Window | None = None
        self.area: Gtk.GLArea | None = None
        self._last_frame_us = 0
        self.use_layer_shell = LAYER_SHELL_AVAILABLE and GtkLayerShell.is_supported()

    # -- creation -----------------------------------------------------------

    def create(self, output: OutputInfo, on_frame: Callable[[float], None]) -> None:
        self._on_frame = on_frame
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title(APPLICATION_ID)
        window.set_name(APPLICATION_ID)
        window.set_wmclass(APPLICATION_ID, APPLICATION_ID)
        window.set_app_paintable(True)
        window.set_decorated(False)
        window.set_accept_focus(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)

        # Without an RGBA visual the surface is opaque and the desktop vanishes
        # behind a grey rectangle.
        screen = window.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            window.set_visual(visual)

        if self.use_layer_shell:
            self._setup_layer_shell(window, output)
        else:
            self._setup_x11(window, output)

        area = Gtk.GLArea()
        area.set_has_alpha(True)
        area.set_has_depth_buffer(True)
        area.set_required_version(3, 3)
        area.set_auto_render(False)  # the frame clock drives rendering, not GTK
        area.connect("realize", self._handle_realize)
        area.connect("render", self._handle_render)
        window.add(area)

        window.connect("realize", self._apply_click_through)
        window.connect("map-event", self._apply_click_through)
        window.connect("size-allocate", self._apply_click_through)
        window.show_all()
        # The input region has to survive every commit the toolkit makes, and
        # the surface only exists once the window is shown, so it is applied
        # again here and on every map and reconfigure. Getting this wrong is not
        # subtle: the overlay swallows every click on whatever it covers.
        self._apply_click_through(window)
        area.add_tick_callback(self._handle_tick)

        self.window = window
        self.area = area

    def _setup_layer_shell(self, window: Gtk.Window, output: OutputInfo) -> None:
        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_namespace(window, APPLICATION_ID)
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(window, edge, True)
        # -1 means "do not reserve space and do not respect anyone else's":
        # the fly walks over panels as happily as over windows.
        GtkLayerShell.set_exclusive_zone(window, -1)
        GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
        monitor = _gdk_monitor_for(output)
        if monitor is not None:
            GtkLayerShell.set_monitor(window, monitor)

    def _setup_x11(self, window: Gtk.Window, output: OutputInfo) -> None:
        window.set_type_hint(Gdk.WindowTypeHint.DOCK)
        window.set_keep_above(True)
        window.stick()
        window.move(output.x, output.y)
        window.set_default_size(output.width, output.height)
        window.resize(output.width, output.height)

    def _apply_click_through(self, window: Gtk.Window, *_: object) -> None:
        """Give the surface an empty input region, so every event passes through.

        On Wayland this becomes wl_surface.set_input_region with an empty
        region; on X11 it is the same call through the Shape extension.
        """
        gdk_window = window.get_window()
        if gdk_window is None:
            return
        gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)

    def _handle_realize(self, area: Gtk.GLArea) -> None:
        area.make_current()
        error = area.get_error()
        if error is not None:
            raise RuntimeError(f"GLArea: {error.message}")
        self._on_realize()

    def _handle_render(self, area: Gtk.GLArea, context: Gdk.GLContext) -> bool:
        self._on_render()
        return True

    def _handle_tick(self, widget: Gtk.Widget, clock: Gdk.FrameClock) -> bool:
        now = clock.get_frame_time()  # microseconds, monotonic
        dt = 0.0 if self._last_frame_us == 0 else (now - self._last_frame_us) / 1_000_000
        self._last_frame_us = now
        if self._on_frame is not None and dt > 0:
            self._on_frame(dt)
        widget.queue_render()
        return True  # GLib.SOURCE_CONTINUE

    # -- geometry -----------------------------------------------------------

    def geometry(self) -> tuple[int, int]:
        if self.area is None:
            return (1, 1)
        scale = self.area.get_scale_factor()
        allocation = self.area.get_allocation()
        return (allocation.width * scale, allocation.height * scale)

    def move_to(self, output: OutputInfo) -> None:
        """Upstream's 'Move to Next Display'.

        A layer surface is bound to its output when it is mapped, so hopping
        means rebuilding the surface rather than moving a window.
        """
        if self.window is None or self._on_frame is None:
            return
        on_frame = self._on_frame
        self.destroy()
        self.create(output, on_frame)

    def destroy(self) -> None:
        if self.window is not None:
            self.window.destroy()
        self.window = None
        self.area = None
        self._last_frame_us = 0
