"""The interactive brain-map window, opened from the tray menu.

Replaces upstream's floating NSPanel. This is a plain xdg-toplevel, not a layer
surface, because it needs real pointer input: dragging orbits, scrolling
dollies, clicking stimulates, and double-clicking goes fullscreen. The
compositor decides whether it tiles or floats; the README ships the Hyprland
rule for anyone who wants it floating.

Upstream's fullscreen mode is a pile of AppKit workarounds - a non-activating
panel dropped one window level, a menu-bar inset, an overridden frame
constraint - none of which has a Linux counterpart. A GTK toplevel fullscreens
normally, and the layer-shell overlay the fly lives on is above it by protocol,
so the fly still walks across the brain and clicks still reach it.
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

# How far the pointer may travel between press and release and still count as a
# click. Above it the gesture was an orbit, so aiming at a neuron and spinning
# the brain never steal each other's gesture.
DRAG_THRESHOLD_PX = 3.0

# Precise scrolling devices report deltas an order of magnitude larger than a
# notched wheel; one flick would otherwise cross the whole dolly range.
SMOOTH_SCROLL_SCALE = 0.12

HINT_LINES = (
    "Drag to rotate · Scroll to zoom",
    "Click to stimulate · Double-click for fullscreen",
)


class BrainWindow:
    def __init__(self, points: BrainPoints, sim: LIFSim, size: tuple[int, int] = (480, 400)):
        self.renderer = BrainRenderer(points, sim)
        self._label_timeout = 0
        self._initialised = False
        self._drag_from: tuple[float, float] | None = None
        self._drag_travel = 0.0
        self._double_click = False
        self.fullscreen = False

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
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.area.connect("realize", self._handle_realize)
        self.area.connect("render", self._handle_render)
        self.area.connect("button-press-event", self._handle_press)
        self.area.connect("button-release-event", self._handle_release)
        self.area.connect("motion-notify-event", self._handle_drag)
        self.area.connect("scroll-event", self._handle_scroll)
        self.area.connect("enter-notify-event", self._handle_enter)
        self.area.connect("leave-notify-event", self._handle_leave)

        self.label = Gtk.Label(label="")
        self.label.set_halign(Gtk.Align.CENTER)
        self.label.set_valign(Gtk.Align.END)
        self.label.set_margin_bottom(10)
        self.label.set_no_show_all(True)

        # Two single-line labels, pinned to the top-left and transparent to the
        # mouse, so the hint can never eat a stimulation click in the corner it
        # occupies.
        self.hint = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.hint.set_halign(Gtk.Align.START)
        self.hint.set_valign(Gtk.Align.START)
        self.hint.set_margin_start(10)
        self.hint.set_margin_top(8)
        self.hint.set_no_show_all(True)
        for line in HINT_LINES:
            label = Gtk.Label(label=line)
            label.set_halign(Gtk.Align.START)
            self.hint.pack_start(label, False, False, 0)

        overlay = Gtk.Overlay()
        overlay.add(self.area)
        overlay.add_overlay(self.label)
        overlay.add_overlay(self.hint)
        overlay.set_overlay_pass_through(self.label, True)
        overlay.set_overlay_pass_through(self.hint, True)
        self.window.add(overlay)
        self.hint_visible = True

        self.area.add_tick_callback(self._handle_tick)
        self._last_frame_us = 0

    # -- window state -------------------------------------------------------

    @property
    def visible(self) -> bool:
        return bool(self.window.get_visible())

    def show(self) -> None:
        self.window.show_all()
        self.label.set_visible(False)
        self._apply_hint()

    def hide(self) -> None:
        self.window.hide()

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def _handle_close(self, *_: object) -> bool:
        self.hide()
        return True  # stop the default handler from destroying the window

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.window.fullscreen()
        else:
            self.window.unfullscreen()

    def toggle_hint(self) -> None:
        """Dismiss the controls hint once the controls are familiar."""
        self.hint_visible = not self.hint_visible
        self._apply_hint()

    def _apply_hint(self) -> None:
        if self.hint_visible:
            self.hint.show_all()
        else:
            self.hint.hide()

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

    def _handle_press(self, _area: Gtk.GLArea, event: object) -> bool:
        self._drag_from = (event.x, event.y)
        if event.type != Gdk.EventType._2BUTTON_PRESS:
            self._drag_travel = 0.0
        else:
            self._double_click = True
        return True

    def _handle_drag(self, _area: Gtk.GLArea, event: object) -> bool:
        if self._drag_from is None:
            return True
        dx, dy = event.x - self._drag_from[0], event.y - self._drag_from[1]
        self._drag_from = (event.x, event.y)
        self._drag_travel += abs(dx) + abs(dy)
        self.renderer.orbit(dx, dy)
        return True

    def _handle_release(self, area: Gtk.GLArea, event: object) -> bool:
        travelled, doubled = self._drag_travel, self._double_click
        self._drag_from, self._double_click = None, False
        if travelled >= DRAG_THRESHOLD_PX:
            return True  # that was a spin, not a stimulation
        if doubled:
            self.toggle_fullscreen()
            return True
        # Stimulation fires immediately rather than waiting out the
        # double-click interval: a delayed spike would defeat the point of
        # watching the fly react. The first click of a double-click therefore
        # still stimulates.
        scale = area.get_scale_factor()
        name = self.renderer.stimulate(self.renderer.pick(event.x * scale, event.y * scale))
        if name:
            self._show_label(name)
        return True

    def _handle_scroll(self, _area: Gtk.GLArea, event: object) -> bool:
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            found, _dx, dy = event.get_scroll_deltas()
            if found:
                self.renderer.zoom(-dy * SMOOTH_SCROLL_SCALE)
        elif event.direction == Gdk.ScrollDirection.UP:
            self.renderer.zoom(1.0)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self.renderer.zoom(-1.0)
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
