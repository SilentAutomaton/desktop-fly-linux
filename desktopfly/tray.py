"""The tray icon and its menu — this fork's stand-in for upstream's NSStatusItem.

The menu carries the same items as the macOS menu bar, and it is how the
interactive brain map is opened. Every item calls the same handler table as the
control socket, so the tray and `desktop-fly ctl` can never drift apart.

StatusNotifierItem is the protocol every modern panel speaks: waybar, KDE,
XFCE, and GNOME through its extension. If no panel answers, the app says so once
and keeps running.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

INDICATOR_ID = "desktop-fly"
# A stock icon, so the tray works with no theme installation and no asset files.
INDICATOR_ICON = "applications-science"


def _load_indicator() -> object | None:
    """Ayatana is the maintained fork; the old name is still around on some systems."""
    for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(namespace, "0.1")
            module = __import__(f"gi.repository.{namespace}", fromlist=[namespace])
        except (ImportError, ValueError):
            continue
        return module
    return None


class Tray:
    def __init__(self, commands: dict[str, Callable[[], None]], provenance: str) -> None:
        self.available = False
        self._commands = commands
        indicator_module = _load_indicator()
        if indicator_module is None:
            return

        self._menu = Gtk.Menu()
        self._add_label("Desktop Fly")
        self._add_label(provenance)  # FlyWire v783 · N somas · circuit …
        self._add_separator()
        self._pause_item = self._add_command("Pause", "pause")
        self._add_command("Show / Hide Brain", "brain")
        self._add_command("Fullscreen Brain", "brain-fullscreen")
        self._add_command("Hide Brain Hint", "brain-hint")
        # The item offers the other form, so it reads as an action.
        self._body_item = self._add_command("Body: Stag Beetle", "body")
        self._add_command("Escape Test (loom)", "escape")
        self._add_command("Move to Next Output", "next-output")
        self._add_separator()
        self._add_command("Add Fly", "add-fly")
        self._add_command("Remove Fly", "remove-fly")
        self._add_command("Scare Flies", "scare")
        self._add_separator()
        self._add_command("Quit", "quit")
        self._menu.show_all()

        self._indicator = indicator_module.Indicator.new(
            INDICATOR_ID, INDICATOR_ICON, indicator_module.IndicatorCategory.APPLICATION_STATUS
        )
        self._indicator.set_status(indicator_module.IndicatorStatus.ACTIVE)
        self._indicator.set_title("Desktop Fly")
        self._indicator.set_menu(self._menu)
        self.available = True

    def set_paused(self, paused: bool) -> None:
        if self.available:
            self._pause_item.set_label("Resume" if paused else "Pause")

    def set_body(self, beetle: bool) -> None:
        if self.available:
            self._body_item.set_label("Body: Fruit Fly" if beetle else "Body: Stag Beetle")

    def _add_label(self, text: str) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=text)
        item.set_sensitive(False)
        self._menu.append(item)
        return item

    def _add_separator(self) -> None:
        self._menu.append(Gtk.SeparatorMenuItem())

    def _add_command(self, text: str, command: str) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=text)
        item.connect("activate", lambda _item: self._commands[command]())
        self._menu.append(item)
        return item
