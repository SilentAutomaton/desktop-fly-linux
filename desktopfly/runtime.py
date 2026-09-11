"""Assembling the running application: overlay, brain map, tray, control socket.

Port of AppDelegate in main.swift. This is the only module that owns the
toolkit, the GL context and the main loop; everything it drives is testable
without any of them.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from desktopfly.app import Coordinator  # noqa: E402
from desktopfly.config import Config  # noqa: E402
from desktopfly.dataset import load_brain_data  # noqa: E402
from desktopfly.geometry import BodyForm  # noqa: E402
from desktopfly.platform.base import Backends, OutputInfo  # noqa: E402
from desktopfly.platform.detect import detect  # noqa: E402
from desktopfly.platform.gtk_brain_window import BrainWindow  # noqa: E402
from desktopfly.platform.gtk_overlay import APPLICATION_ID, GtkOverlay  # noqa: E402
from desktopfly.render.gl import Renderer  # noqa: E402
from desktopfly.sim import SpikeBus  # noqa: E402
from desktopfly.tray import Tray  # noqa: E402

log = logging.getLogger("desktopfly")

SOCKET_NAME = "desktop-fly.sock"


def control_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(runtime) if runtime else Path("/tmp")
    return root / SOCKET_NAME


class Application:
    def __init__(self, config: Config, backends: Backends | None = None) -> None:
        # GTK 3 takes the Wayland app_id from the program name, and Gtk.Window
        # .set_wmclass only reaches X11. Without this the brain toplevel comes
        # up as "__main__.py", which no window rule matches and which the
        # backends' own-surface filters do not recognise as ours.
        GLib.set_prgname(APPLICATION_ID)

        self.config = config
        self.backends = backends or detect(taps_enabled=config.senses.input_devices != "off")
        self.data = load_brain_data()
        if self.data is None:
            log.warning("no FlyWire data found — falling back to legacy behaviour")

        output = self._pick_output()
        if output is None:
            raise SystemExit("no output found: is a compositor running?")

        self.spike_bus = SpikeBus() if self.data is not None else None
        self.coordinator = Coordinator(config, self.backends, self.data, output, self.spike_bus)

        self.renderer = Renderer()
        self.overlay = GtkOverlay(on_render=self._render, on_realize=self._realize)
        self.overlay.create(output, self.coordinator.frame)

        self.brain: BrainWindow | None = None
        if self.data is not None and self.coordinator.sim is not None:
            self.brain = BrainWindow(
                self.data.points, self.coordinator.sim, config.brain.window_size
            )
            if config.brain.show_on_start:
                self.brain.show()

        self.commands: dict[str, Callable[[], None]] = {
            "pause": self._toggle_pause,
            "resume": self._resume,
            "brain": self._toggle_brain,
            "brain-fullscreen": self._toggle_brain_fullscreen,
            "brain-hint": self._toggle_brain_hint,
            "body": self._toggle_body,
            "escape": self.coordinator.escape_test,
            "scare": self.coordinator.scare_all,
            "add-fly": self.coordinator.add_fly,
            "remove-fly": self.coordinator.remove_fly,
            "next-output": self._next_output,
            "quit": self.quit,
        }
        provenance = self.data.describe() if self.data else "no FlyWire data"
        self.tray = Tray(self.commands, provenance)
        self.tray.set_body(self.coordinator.form is BodyForm.BEETLE)
        if not self.tray.available:
            log.info("no tray host answered — drive the fly with `desktop-fly ctl <command>`")

        self._serve_control_socket()

    # -- output selection ---------------------------------------------------

    def _pick_output(self) -> OutputInfo | None:
        outputs = self.backends.outputs.outputs()
        wanted = self.config.display.output
        if wanted:
            chosen = next((o for o in outputs if o.name == wanted), None)
            if chosen is not None:
                return chosen
            log.warning("output %r not found; using the current one", wanted)
        return self.backends.outputs.current() or (outputs[0] if outputs else None)

    def _next_output(self) -> None:
        outputs = self.backends.outputs.outputs()
        if len(outputs) < 2:
            return
        names = [o.name for o in outputs]
        index = (
            names.index(self.coordinator.output.name)
            if (self.coordinator.output.name in names)
            else 0
        )
        target = outputs[(index + 1) % len(outputs)]
        self.coordinator.retarget(target)
        self.overlay.move_to(target)

    # -- rendering ----------------------------------------------------------

    def _realize(self) -> None:
        self.renderer.initialise()

    def _render(self) -> None:
        width, height = self.overlay.geometry()
        self.renderer.resize(width, height)
        anchors = self.coordinator.shadow_anchors() if self.config.display.shadows else []
        self.renderer.draw(self.coordinator.scene_nodes(), anchors)

    # -- commands -----------------------------------------------------------

    def _toggle_pause(self) -> None:
        self.coordinator.toggle_pause()
        self.tray.set_paused(self.coordinator.paused)

    def _resume(self) -> None:
        self.coordinator.paused = False
        self.tray.set_paused(False)

    def _toggle_brain(self) -> None:
        if self.brain is not None:
            self.brain.toggle()

    def _toggle_brain_fullscreen(self) -> None:
        if self.brain is not None:
            self.brain.toggle_fullscreen()

    def _toggle_brain_hint(self) -> None:
        if self.brain is not None:
            self.brain.toggle_hint()

    def _toggle_body(self) -> None:
        self.coordinator.toggle_body()
        self.tray.set_body(self.coordinator.form is BodyForm.BEETLE)

    def quit(self) -> None:
        Gtk.main_quit()

    # -- control socket -----------------------------------------------------

    def _serve_control_socket(self) -> None:
        """A one-line-per-command unix socket, so the menu can be bound to keys.

        This is what makes the app usable on a tiling compositor without ever
        reaching for the tray: `bind = SUPER, F, exec, desktop-fly ctl scare`.
        """
        path = control_socket_path()
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(4)
        server.setblocking(False)
        self._control_socket = server
        GLib.io_add_watch(server.fileno(), GLib.IO_IN, self._handle_control_connection)

    def _handle_control_connection(self, *_: object) -> bool:
        try:
            client, _address = self._control_socket.accept()
        except OSError:
            return True
        with client:
            client.settimeout(0.2)
            try:
                command = client.recv(64).decode(errors="replace").strip()
            except OSError:
                return True
            action = self.commands.get(command)
            if action is None:
                client.sendall(f"unknown command: {command}\n".encode())
            else:
                action()
                client.sendall(b"ok\n")
        return True

    def run(self) -> int:
        try:
            Gtk.main()
        finally:
            control_socket_path().unlink(missing_ok=True)
        return 0


def send_command(command: str) -> int:
    """Client side of `desktop-fly ctl`."""
    path = control_socket_path()
    if not path.exists():
        print("desktop-fly is not running", flush=True)
        return 1
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(path))
            client.sendall(command.encode())
            print(client.recv(256).decode(errors="replace").strip())
    except OSError as error:
        print(f"could not reach desktop-fly: {error}")
        return 1
    return 0
