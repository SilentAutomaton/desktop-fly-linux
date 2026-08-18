"""The interactive brain-map window, opened from the tray menu.

Replaces upstream's floating NSPanel. This is a plain xdg-toplevel, not a layer
surface, because it needs real pointer input: hovering holds the rotation still
and clicking stimulates the neurons under the cursor. The compositor decides
whether it tiles or floats; the README ships the Hyprland rule for anyone who
wants it floating.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from desktopfly.dataset import BrainPoints  # noqa: E402
from desktopfly.render.brain import BrainRenderer  # noqa: E402
from desktopfly.sim import LIFSim  # noqa: E402

WINDOW_CLASS = "desktop-fly-brain"
TITLE = "Fly Brain — FlyWire v783 (click = stimulate)"
LABEL_SECONDS = 2.2


class BrainWindow:
    def __init__(self, points: BrainPoints, sim: LIFSim, size: tuple[int, int] = (480, 400)):
        self.renderer = BrainRenderer(points, sim)
        self._label_timeout = 0
        self._initialised = False

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title(TITLE)
        self.window.set_wmclass(WINDOW_CLASS, WINDOW_CLASS)
        self.window.set_name(WINDOW_CLASS)
        self.window.set_default_size(*size)
        # Closing the window only hides it: the simulation and the tray toggle
        # both expect it to still exist.
        self.window.connect("delete-event", self._handle_close)

        self.area = Gtk.GLArea()
        self.area.set_required_version(3, 3)
        self.area.set_has_depth_buffer(True)
        self.area.set_auto_render(False)
        self.area.set_events(
            self.area.get_events()
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.area.connect("realize", self._handle_realize)
        self.area.connect("render", self._handle_render)
        self.area.connect("button-press-event", self._handle_click)
        self.area.connect("enter-notify-event", self._handle_enter)
        self.area.connect("leave-notify-event", self._handle_leave)

        self.label = Gtk.Label(label="")
        self.label.set_halign(Gtk.Align.CENTER)
        self.label.set_valign(Gtk.Align.END)
        self.label.set_margin_bottom(10)
        self.label.set_no_show_all(True)

        overlay = Gtk.Overlay()
        overlay.add(self.area)
        overlay.add_overlay(self.label)
        self.window.add(overlay)

        self.area.add_tick_callback(self._handle_tick)
        self._last_frame_us = 0

    # -- window state -------------------------------------------------------

    @property
    def visible(self) -> bool:
        return bool(self.window.get_visible())

    def show(self) -> None:
        self.window.show_all()
        self.label.set_visible(False)

    def hide(self) -> None:
        self.window.hide()

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def _handle_close(self, *_: object) -> bool:
        self.hide()
        return True  # stop the default handler from destroying the window

    # -- GL -----------------------------------------------------------------

    def _handle_realize(self, area: Gtk.GLArea) -> None:
        area.make_current()
        if area.get_error() is not None:
            raise RuntimeError(f"brain GLArea: {area.get_error().message}")
        self.renderer.initialise()
        self._initialised = True

    def _handle_render(self, area: Gtk.GLArea, _context: object) -> bool:
        if not self._initialised:
            return True
        scale = area.get_scale_factor()
        allocation = area.get_allocation()
        self.renderer.resize(allocation.width * scale, allocation.height * scale)
        self.renderer.draw()
        return True

    def _handle_tick(self, widget: Gtk.Widget, clock: object) -> bool:
        now = clock.get_frame_time()
        dt = 0.0 if self._last_frame_us == 0 else (now - self._last_frame_us) / 1_000_000
        self._last_frame_us = now
        if dt > 0:
            self.renderer.step(dt)
        widget.queue_render()
        return True

    # -- interaction --------------------------------------------------------

    def _handle_click(self, area: Gtk.GLArea, event: object) -> bool:
        scale = area.get_scale_factor()
        picked = self.renderer.pick(event.x * scale, event.y * scale)
        name = self.renderer.stimulate(picked)
        if name:
            self._show_label(name)
        return True

    def _handle_enter(self, *_: object) -> bool:
        # Hold the rotation while the pointer is inside, so a region can be aimed
        # at instead of chased.
        self.renderer.paused = True
        return False

    def _handle_leave(self, *_: object) -> bool:
        self.renderer.paused = False
        return False

    def _show_label(self, text: str) -> None:
        self.label.set_text(text)
        self.label.set_visible(True)
        if self._label_timeout:
            GLib.source_remove(self._label_timeout)
        self._label_timeout = GLib.timeout_add(int(LABEL_SECONDS * 1000), self._hide_label)

    def _hide_label(self) -> bool:
        self.label.set_visible(False)
        self._label_timeout = 0
        return False
