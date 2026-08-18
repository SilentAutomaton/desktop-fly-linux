"""The frame loop: desktop senses in, neurons in the middle, flies out.

Port of the Coordinator class in main.swift together with the sense timers
upstream keeps in AppDelegate. Upstream funnels every cross-thread mutation
through a lock and a pending-action queue because macOS timers, the menu and
the global click monitor all live on different threads from the SceneKit render
loop. Here the frame callback, the sensor polls, the tray menu and the control
socket are all callbacks of one GTK main loop and cannot interleave, so that
machinery is dropped on purpose. Polling is done with accumulators inside
frame() rather than separate timers, for the same reason.

This module renders nothing and imports no toolkit.
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime

from desktopfly import constants as k
from desktopfly.behavior import Fly, State
from desktopfly.config import Config
from desktopfly.dataset import BrainData
from desktopfly.environment import (
    OutputInfo,
    TypingLevel,
    WindowSense,
    circadian_activity,
    is_sleepy,
    thermal_tempo,
)
from desktopfly.platform.base import Backends
from desktopfly.signals import SignalBuilder
from desktopfly.sim import BrainSignals, LIFSim, SpikeBus


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


class Coordinator:
    def __init__(
        self,
        config: Config,
        backends: Backends,
        data: BrainData | None,
        output: OutputInfo,
        spike_bus: SpikeBus | None = None,
    ) -> None:
        self.config = config
        self.backends = backends
        self.output = output
        self.paused = False

        self.sim: LIFSim | None = None
        if data is not None and config.sim.enabled:
            self.sim = LIFSim(data.circuit, spike_bus=spike_bus, seed=config.sim.seed)
        self._signal_builder = SignalBuilder()
        self._window_sense = WindowSense()
        self._typing = TypingLevel()

        self.flies: list[Fly] = []
        for _ in range(max(1, config.fly.count)):
            self.add_fly()

        # Sensory state carried between frames.
        self._mouse: tuple[float, float] | None = None
        self._previous_mouse: tuple[float, float] | None = None
        self._mouse_velocity = (0.0, 0.0)
        self._window_loom_left = 0.0
        self._window_loom_right = 0.0
        self._loom_override = 0.0
        self._sim_ms_accumulator = 0.0

        # Poll accumulators, so no separate timers are needed.
        self._pointer_timer = 0.0
        self._window_timer = 0.0
        self._last_pointer_move = time.monotonic()
        self._last_window_change = time.monotonic()
        self._last_key_time = 0.0
        self._last_input = 0.0
        self._tempo = 1.0
        self._activity = 1.0
        self._sleepy = False

    # -- geometry -----------------------------------------------------------

    @property
    def bounds(self) -> tuple[float, float]:
        return (float(self.output.width), float(self.output.height))

    def retarget(self, output: OutputInfo) -> None:
        """Follow the fly to another output. Port of AppDelegate.move(to:)."""
        self.output = output
        width, height = self.bounds
        for fly in self.flies:
            fly.terrain = []  # stale until the next window poll
            fly.ledge = None
            fly.x = clamp(fly.x, -width / 2 + 40, width / 2 - 40)
            fly.y = clamp(fly.y, -height / 2 + 40, height / 2 - 40)

    # -- commands, shared by the tray menu and the control socket ------------

    def add_fly(self) -> None:
        width, height = self.bounds
        half_width, half_height = width / 2 - 100, height / 2 - 100
        self.flies.append(
            Fly(
                (random.uniform(-half_width, half_width), random.uniform(-half_height, half_height))
            )
        )

    def remove_fly(self) -> None:
        if len(self.flies) > 1:  # fly #1 is the one carrying the brain
            self.flies.pop()

    def escape_test(self) -> None:
        """Inject a real looming stimulus, then let the circuit decide."""
        self._loom_override = k.ESCAPE_TEST_LOOM

    def scare_all(self) -> None:
        self.escape_test()
        for fly in self.flies[1:]:
            if fly.state is not State.FLYING:
                fly.start_flight(self.bounds)

    def toggle_pause(self) -> None:
        self.paused = not self.paused

    # -- senses -------------------------------------------------------------

    def _poll_pointer(self) -> None:
        position = self.backends.pointer.position()
        if position is None:
            self._mouse = None
            return
        scene = self.output.to_scene(*position)
        if self._mouse is None or math.dist(scene, self._mouse) > 0.5:
            self._last_pointer_move = time.monotonic()
        self._mouse = scene

    def _poll_windows(self) -> None:
        if not self.config.senses.ledges:
            return
        snapshot = self._window_sense.poll(self.backends.windows.rects(), self.output)
        for fly in self.flies:
            fly.terrain = snapshot.ledges
        if snapshot.new_windows:
            self._last_window_change = time.monotonic()
        for window in snapshot.new_windows:
            # A window appearing near the fly is a looming object, and the
            # circuit decides on its own whether to flee the dialog.
            distance = math.dist(window.center, (self.flies[0].x, self.flies[0].y))
            strength = (
                clamp(1 - distance / k.WINDOW_LOOM_FALLOFF_PX, 0.0, 1.0) * k.WINDOW_LOOM_SCALE
            )
            if strength > k.WINDOW_LOOM_MIN:
                self._inject_window_loom(strength, window.center)

    def _poll_activity(self) -> None:
        activity = self.backends.taps.drain()
        now = time.monotonic()
        if activity.keys:
            self._last_key_time = now
        if activity.keys or activity.taps:
            self._last_input = now
        for _ in range(min(activity.taps, 4)):  # a burst of clicks is still one startle
            self._inject_tap()

    def _poll_ambient(self) -> None:
        now = datetime.now()
        hour = now.hour + now.minute / 60
        # Upstream asks macOS for the seconds since the user last touched
        # anything. Here that is reconstructed from the senses that exist: the
        # cursor moving, the window list changing, and - when permitted - a real
        # key or button going down.
        now = time.monotonic()
        idle = min(
            now - self._last_pointer_move,
            now - self._last_window_change,
            now - self._last_input if self._last_input else float("inf"),
        )
        self._tempo = thermal_tempo(self.backends.thermal.load())
        if self.config.circadian.enabled:
            self._activity = circadian_activity(hour)
            self._sleepy = is_sleepy(idle, hour)
        else:
            self._activity, self._sleepy = 1.0, False

    def _inject_window_loom(self, strength: float, position: tuple[float, float]) -> None:
        fly = self.flies[0]
        relative = (position[0] - fly.x, position[1] - fly.y)
        distance = max(1.0, math.hypot(*relative))
        forward = (math.cos(fly.heading), math.sin(fly.heading))
        cross = (forward[0] * relative[1] - forward[1] * relative[0]) / distance
        self._window_loom_left = max(
            self._window_loom_left, strength * clamp(0.5 + 0.5 * cross, k.LOOM_EYE_FLOOR, 1.0)
        )
        self._window_loom_right = max(
            self._window_loom_right, strength * clamp(0.5 - 0.5 * cross, k.LOOM_EYE_FLOOR, 1.0)
        )

    def _inject_tap(self) -> None:
        """A click somewhere on the desktop is a tap on the fly's substrate."""
        if self.sim is None or self._mouse is None:
            return
        fly = self.flies[0]
        distance = math.dist(self._mouse, (fly.x, fly.y))
        strength = clamp(1 - distance / k.TAP_FALLOFF_PX, 0.0, 1.0)
        if strength > k.TAP_MIN_STRENGTH:
            self.sim.stimulate(
                self.sim.groups.sensory,
                k.TAP_STIM_BASE + strength * k.TAP_STIM_GAIN,
                k.TAP_STIM_MS,
            )

    def _compute_loom(self, fly: Fly, dt: float) -> tuple[float, float, float]:
        """Cursor kinematics into looming drive per eye, plus an air puff.

        Port of Coordinator.computeLoom. This is the sensory transduction step:
        everything downstream of the LC4/LPLC2 population is the real
        connectome, and this is the only place a desktop event becomes a
        stimulus.
        """
        if self._mouse is None:
            return (0.0, 0.0, 0.0)
        if self._previous_mouse is not None and dt > 0:
            velocity = (
                (self._mouse[0] - self._previous_mouse[0]) / dt,
                (self._mouse[1] - self._previous_mouse[1]) / dt,
            )
            self._mouse_velocity = (
                self._mouse_velocity[0]
                + (velocity[0] - self._mouse_velocity[0]) * k.MOUSE_VELOCITY_ALPHA,
                self._mouse_velocity[1]
                + (velocity[1] - self._mouse_velocity[1]) * k.MOUSE_VELOCITY_ALPHA,
            )
        self._previous_mouse = self._mouse

        relative = (self._mouse[0] - fly.x, self._mouse[1] - fly.y)
        distance = max(k.LOOM_MIN_DISTANCE, math.hypot(*relative))
        # Radial approach speed: positive means the cursor is closing in.
        approach = (
            -(relative[0] * self._mouse_velocity[0] + relative[1] * self._mouse_velocity[1])
            / distance
        )
        loom = clamp(approach / distance * k.LOOM_APPROACH_GAIN, 0.0, 1.0) * clamp(
            1 - distance / k.LOOM_FALLOFF_PX, 0.0, 1.0
        )
        loom += clamp((k.LOOM_NEAR_PX - distance) / k.LOOM_NEAR_PX, 0.0, 1.0) * k.LOOM_NEAR_WEIGHT
        loom = clamp(loom + self._loom_override, 0.0, 1.0)

        # Split between the eyes by bearing: a threat on the left drives the
        # left looming population harder, which is what steers the escape.
        forward = (math.cos(fly.heading), math.sin(fly.heading))
        direction = (relative[0] / distance, relative[1] / distance)
        cross = forward[0] * direction[1] - forward[1] * direction[0]
        left = clamp(0.5 + 0.5 * cross, k.LOOM_EYE_FLOOR, 1.0)
        right = clamp(0.5 - 0.5 * cross, k.LOOM_EYE_FLOOR, 1.0)
        puff = clamp(math.hypot(*self._mouse_velocity) / k.AIR_PUFF_SPEED_PX_S, 0.0, 1.0) * clamp(
            1 - distance / k.AIR_PUFF_FALLOFF_PX, 0.0, 1.0
        )
        return (loom * left, loom * right, puff)

    # -- frame --------------------------------------------------------------

    def frame(self, dt: float) -> None:
        if self.paused:
            return
        dt = min(k.MAX_FRAME_DT_S, max(0.0, dt))

        self._pointer_timer += dt
        pointer_interval = 1.0 / max(1.0, self.config.senses.pointer_poll_hz)
        if self._pointer_timer >= pointer_interval:
            self._pointer_timer = 0.0
            self._poll_pointer()
            self._poll_activity()
            self._poll_ambient()

        self._window_timer += dt
        window_interval = 1.0 / max(0.1, self.config.senses.window_poll_hz)
        if self._window_timer >= window_interval:
            self._window_timer = 0.0
            self._poll_windows()

        signals: BrainSignals | None = None
        if self.sim is not None:
            signals = self._step_brain(dt)

        for index, fly in enumerate(self.flies):
            fly.update(dt, self.bounds, self._mouse, signals if index == 0 else None)

    def _step_brain(self, dt: float) -> BrainSignals:
        assert self.sim is not None
        sim = self.sim
        first = self.flies[0]
        loom_left, loom_right, puff = self._compute_loom(first, dt)

        decay = math.exp(-k.WINDOW_LOOM_DECAY_PER_S * dt)
        self._window_loom_left *= decay
        self._window_loom_right *= decay
        sim.loom_left = max(loom_left, self._window_loom_left)
        sim.loom_right = max(loom_right, self._window_loom_right)

        typing = self._typing.update(time.monotonic() - self._last_key_time)
        sim.air_puff = max(puff, typing * k.TYPING_AIR_PUFF)

        # Body to brain: leg proprioception from the gait the fly is walking.
        sim.gait_drive = first.walking_intensity
        sim.gait_phase = first.gait_phase

        # Circadian and sleep neuromodulation, compressed rather than raw: the
        # neurons sit just below threshold, so a plain multiplier silences them.
        sim.activity_scale = (1 - (1 - self._activity) * k.CIRCADIAN_COMPRESSION) * (
            k.SLEEP_ACTIVITY_SCALE if self._sleepy else 1.0
        )
        sim.sensory_gate = k.SLEEP_SENSORY_GATE if self._sleepy else 1.0

        self._loom_override = max(0.0, self._loom_override - dt * k.ESCAPE_TEST_DECAY_PER_S)

        self._sim_ms_accumulator += dt * 1000
        steps = min(self.config.sim.max_step_ms, int(self._sim_ms_accumulator))
        self._sim_ms_accumulator -= steps
        sim.step(steps)

        signals = self._signal_builder.make(sim, dt)
        signals.tempo = self._tempo
        signals.sleep = self._sleepy
        return signals

    # -- what the renderer needs -------------------------------------------

    def scene_nodes(self) -> list:  # type: ignore[type-arg]
        return [fly.node for fly in self.flies]

    def shadow_anchors(self) -> list[tuple[float, float, float]]:
        return [(fly.x, fly.y, fly.altitude) for fly in self.flies]
