"""The fly's body: what it does, and how the legs and wings follow.

Port of the Fly class in FlyModel.swift. Every decision in brain_behavior()
reads a real neuron population's firing rate; nothing here is scripted.

The class is deliberately free of any renderer: it writes into a scenegraph
Node tree and nothing else, which is what lets the behaviour test suite run
headless.
"""

from __future__ import annotations

import math
import random
from enum import Enum

from . import constants as k
from .environment import Ledge
from .geometry import FlyModel, build_fly_model
from .scenegraph import Node
from .sim import BrainSignals


class State(Enum):
    WALKING = "walking"
    IDLE = "idle"
    GROOMING = "grooming"
    FLYING = "flying"
    SLEEPING = "sleeping"


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def angle_difference(from_angle: float, to_angle: float) -> float:
    """The shortest signed turn from one heading to another."""
    difference = math.fmod(to_angle - from_angle, 2 * math.pi)
    if difference > math.pi:
        difference -= 2 * math.pi
    if difference < -math.pi:
        difference += 2 * math.pi
    return difference


def smoothstep(t: float) -> float:
    x = clamp(t, 0.0, 1.0)
    return x * x * (3 - 2 * x)


class Fly:
    def __init__(self, position: tuple[float, float]):
        self.model: FlyModel = build_fly_model()
        self.x, self.y = position
        self.heading = random.uniform(0.0, 2 * math.pi)
        self.speed = 30.0
        self.state = State.WALKING
        self.state_timer = random.uniform(1.5, 4.0)
        self.gait_phase = random.uniform(0.0, 1.0)
        self.time = random.uniform(0.0, 100.0)
        self.scare_cooldown = 0.0
        self.dart_cooldown = 0.0
        self.backward_timer = 0.0
        self.dart_timer = 0.0
        self.state_age = 0.0

        self.terrain: list[Ledge] = []  # walkable window edges, set by the coordinator
        self.ledge: Ledge | None = None  # the edge it is currently standing on

        self.flight_from = (0.0, 0.0)
        self.flight_to = (0.0, 0.0)
        self.flight_t = 0.0
        self.flight_duration = 1.0
        self.flight_effort = 0.6  # fixed at takeoff: escape is 1, casual comes from arousal
        self.effort_current = 0.6  # live: base plus ongoing escape-DN and arousal drive
        self.altitude = 0.0  # 0 on the ground, 1 at cruise
        self.pitch = 0.0
        self.flap_phase = 0.0
        self.wing_raise = 0.0  # grounded threat posture

        self._brain_live = False
        self._live_arousal = 0.0
        self._live_wing = 0.0

        self.sync_node()

    # -- readouts the coordinator feeds back into the brain ------------------

    @property
    def node(self) -> Node:
        return self.model.root

    @property
    def walking_intensity(self) -> float:
        if self.state is not State.WALKING:
            return 0.0
        speed = k.BACKWARD_SPEED if self.backward_timer > 0 else self.speed
        return clamp(abs(speed) / 60.0, 0.0, 1.0)

    @property
    def _effective_speed(self) -> float:
        return k.BACKWARD_SPEED if self.backward_timer > 0 else self.speed

    # -- frame --------------------------------------------------------------

    def update(
        self,
        dt: float,
        bounds: tuple[float, float],
        mouse: tuple[float, float] | None,
        signals: BrainSignals | None,
    ) -> None:
        self.time += dt
        self.scare_cooldown = max(0.0, self.scare_cooldown - dt)
        self.dart_cooldown = max(0.0, self.dart_cooldown - dt)
        self.backward_timer = max(0.0, self.backward_timer - dt)
        self.state_age += dt
        self.dart_timer = max(0.0, self.dart_timer - dt)

        # Live brain drives reach the wings even in mid-flight.
        self._brain_live = signals is not None
        self._live_arousal = signals.arousal if signals else 0.0
        self._live_wing = signals.wing_drive if signals else 0.0

        if self.state is State.FLYING:
            self._update_flight(dt)
        elif signals is not None:
            self._brain_behavior(signals, dt, bounds, mouse)
            if self.state is State.WALKING:
                self._update_walk(dt, bounds)
        else:
            self._legacy_behavior(dt, bounds, mouse)

        self._update_legs(dt)
        self._update_wings(dt)
        rate, depth = k.BREATHE_ASLEEP if self.state is State.SLEEPING else k.BREATHE_AWAKE
        self.model.abdomen.scale = [0.9, 1.5, 0.75 * (1 + depth * math.sin(self.time * rate))]
        self.sync_node()

    def sync_node(self) -> None:
        node = self.node
        node.position = [self.x, self.y, node.position[2]]
        node.euler = [self.pitch, 0.0, self.heading - math.pi / 2]

    def _set_state(self, state: State) -> None:
        if state is self.state:
            return
        self.state = state
        self.state_age = 0.0

    # -- connectome-driven behaviour ----------------------------------------

    def _brain_behavior(
        self,
        s: BrainSignals,
        dt: float,
        bounds: tuple[float, float],
        mouse: tuple[float, float] | None,
    ) -> None:
        """Port of FlyModel.swift brainBehavior. Every branch reads a real population."""
        # A giant-fiber spike is an escape takeoff, even out of sleep.
        if s.escape and self.scare_cooldown == 0:
            self.start_flight(bounds, away_from=mouse, escape=True)
            return

        # Circadian sleep: enter, hold, and wake into grooming as a real fly does.
        if s.sleep:
            if self.state is not State.SLEEPING:
                self._set_state(State.SLEEPING)
                self.speed = 0.0
                self.dart_timer = 0.0
                self.backward_timer = 0.0
            return
        if self.state is State.SLEEPING:
            self._set_state(State.GROOMING)
            return

        # Looming detectors hot but the giant fiber quiet: a nervous dart away.
        if s.nervous > k.NERVOUS_DART_THRESHOLD and self.dart_cooldown == 0:
            self.ledge = None
            self._set_state(State.WALKING)
            if mouse is not None:
                self.heading = math.atan2(self.y - mouse[1], self.x - mouse[0]) + random.uniform(
                    -0.4, 0.4
                )
            else:
                self.heading += random.uniform(-1.5, 1.5)
            self.speed = random.uniform(*k.DART_SPEED)
            self.dart_timer = random.uniform(*k.DART_DURATION_S)
            self.dart_cooldown = k.DART_COOLDOWN_S

        # DNg11, the grooming command, with hysteresis on both edges.
        if self.state is not State.WALKING or self.dart_timer == 0:
            if (
                self.state is not State.GROOMING
                and s.groom_drive > k.GROOM_ON
                and s.nervous < k.GROOM_NERVOUS_CEILING
                and self.state_age > k.STATE_DWELL_S
            ):
                self._set_state(State.GROOMING)
            elif (
                self.state is State.GROOMING
                and s.groom_drive < k.GROOM_OFF
                and self.state_age > k.GROOM_OFF_DWELL_S
            ):
                self._set_state(State.IDLE)

        # DNp09, the forward-walking command, likewise.
        if (
            self.state is State.IDLE
            and s.walk_drive > k.WALK_ON
            and self.state_age > k.STATE_DWELL_S
        ):
            self._set_state(State.WALKING)
            self.heading += random.uniform(-0.8, 0.8)
        elif (
            self.state is State.WALKING
            and self.dart_timer == 0
            and s.walk_drive < k.WALK_OFF
            and self.state_age > k.WALK_OFF_DWELL_S
        ):
            self._set_state(State.IDLE)
            self.speed = 0.0

        # An MDN burst scoots it backwards, from any grounded state.
        if s.backward and self.backward_timer == 0 and self.dart_timer == 0:
            if self.state is not State.WALKING:
                self._set_state(State.WALKING)
                self.speed = 0.0
            self.backward_timer = k.BACKWARD_DURATION_S

        if self.state is State.WALKING:
            if self.dart_timer == 0 and self.backward_timer == 0:
                target = (k.WALK_SPEED_BASE + s.walk_drive * k.WALK_SPEED_GAIN) * s.tempo
                self.speed += (target - self.speed) * min(1.0, k.WALK_SPEED_LERP * dt)
            if self.ledge is None:
                self.heading += s.turn_bias * dt  # DNa01/DNa02 steering

        # Spontaneous takeoff, gated on whole-population arousal; how aroused the
        # network is also sets how hard it beats and therefore how high it goes.
        chance = (
            k.FLIGHT_CHANCE_AROUSED if s.arousal > k.FLIGHT_AROUSAL_GATE else k.FLIGHT_CHANCE_CALM
        )
        if self.state is State.WALKING and random.random() < chance * dt:
            self.start_flight(
                bounds, effort=k.FLIGHT_EFFORT_BASE + s.arousal * k.FLIGHT_EFFORT_AROUSAL_GAIN
            )

    def _legacy_behavior(
        self, dt: float, bounds: tuple[float, float], mouse: tuple[float, float] | None
    ) -> None:
        """Distance-based fear for the extra flies, which carry no brain.

        Port of the `signals == nil` path in FlyModel.swift update().
        """
        if self.scare_cooldown == 0 and mouse is not None:
            distance = math.hypot(mouse[0] - self.x, mouse[1] - self.y)
            if distance < k.SCARE_RADIUS:
                self.start_flight(bounds, away_from=mouse)
            elif distance < k.NERVOUS_RADIUS and self.state is not State.WALKING:
                self._set_state(State.WALKING)
                self.heading = math.atan2(self.y - mouse[1], self.x - mouse[0]) + random.uniform(
                    -0.4, 0.4
                )
                self.speed = random.uniform(110.0, 150.0)
                self.state_timer = random.uniform(0.4, 0.9)
                self.scare_cooldown = 1.0
        if self.state is State.FLYING:
            return
        self.state_timer -= dt
        if self.state_timer <= 0:
            if self.state is State.WALKING and random.random() < 0.10:
                self.start_flight(bounds)
            else:
                self._pick_next_state()
        if self.state is State.WALKING:
            self._update_walk(dt, bounds)

    def _pick_next_state(self) -> None:
        if self.state is State.WALKING:
            roll = random.random()
            if roll < 0.30:
                self.state = State.IDLE
                self.state_timer = random.uniform(0.8, 3.0)
                self.speed = 0.0
            elif roll < 0.55:
                self.state_timer = random.uniform(0.3, 0.8)
                self.speed = random.uniform(95.0, 150.0)
                self.heading += random.uniform(-1.2, 1.2)
            else:
                self.state_timer = random.uniform(1.5, 5.0)
                self.speed = random.uniform(18.0, 45.0)
        elif self.state is State.IDLE:
            if random.random() < 0.35:
                self.state = State.GROOMING
                self.state_timer = random.uniform(1.0, 2.5)
            else:
                self.state = State.WALKING
                self.state_timer = random.uniform(1.5, 5.0)
                self.speed = random.uniform(18.0, 45.0)
                self.heading += random.uniform(-1.5, 1.5)
        elif self.state is State.GROOMING:
            self.state = State.IDLE
            self.state_timer = random.uniform(0.3, 1.0)

    # -- walking ------------------------------------------------------------

    def _update_walk(self, dt: float, bounds: tuple[float, float]) -> None:
        width, height = bounds
        if self.ledge is not None and not self._refresh_ledge(bounds):
            return

        if self.ledge is not None:
            ledge = self.ledge
            # Walking an edge means holding a heading of 0 or pi and tracking
            # the edge height; the window may be moving under its feet.
            self.heading += random.uniform(-1.0, 1.0) * k.LEDGE_WANDER * dt
            along = 0.0 if math.cos(self.heading) >= 0 else math.pi
            self.heading += angle_difference(self.heading, along) * min(
                1.0, k.LEDGE_ALIGN_LERP * dt
            )
            self.x += math.cos(self.heading) * self._effective_speed * dt
            self.y += (ledge.y - self.y) * min(1.0, k.LEDGE_SNAP_LERP * dt)
            if self.x <= ledge.x0 + k.LEDGE_END_MARGIN and math.cos(self.heading) < 0:
                self.heading = 0.0
            if self.x >= ledge.x1 - k.LEDGE_END_MARGIN and math.cos(self.heading) > 0:
                self.heading = math.pi
            self.x = clamp(self.x, ledge.x0, ledge.x1)
            if random.random() < k.LEDGE_LEAVE_CHANCE * dt:
                self.ledge = None
        else:
            self.heading += random.uniform(-1.0, 1.0) * k.FREE_WANDER * dt
            half_width = width / 2 - k.EDGE_MARGIN
            half_height = height / 2 - k.EDGE_MARGIN
            if abs(self.x) > half_width or abs(self.y) > half_height:
                to_center = math.atan2(-self.y, -self.x)
                self.heading += angle_difference(self.heading, to_center) * min(
                    1.0, k.BOUNDARY_STEER_LERP * dt
                )
            speed = self._effective_speed
            self.x += math.cos(self.heading) * speed * dt
            self.y += math.sin(self.heading) * speed * dt
            self.x = clamp(self.x, -width / 2 + k.WALL_MARGIN, width / 2 - k.WALL_MARGIN)
            self.y = clamp(self.y, -height / 2 + k.WALL_MARGIN, height / 2 - k.WALL_MARGIN)
            self._maybe_attach_ledge(dt)

        self.node.position[2] = k.GAIT_BOB_Z * abs(math.sin(self.gait_phase * math.pi * 2))

    def _refresh_ledge(self, bounds: tuple[float, float]) -> bool:
        """Follow the window this edge belongs to; take off if it vanished.

        Returns False when the fly left the ground, so the caller stops walking.
        """
        assert self.ledge is not None
        current = next((L for L in self.terrain if L.key == self.ledge.key), None)
        if current is not None and abs(current.y - self.ledge.y) < k.LEDGE_LOST_DISTANCE:
            self.ledge = current
            return True
        self.ledge = None
        self.start_flight(bounds)  # the ground vanished from under its feet
        return False

    def _maybe_attach_ledge(self, dt: float) -> None:
        for ledge in self.terrain:
            near_x = ledge.x0 - 8 < self.x < ledge.x1 + 8
            near_y = abs(self.y - ledge.y) < k.LEDGE_ATTACH_DISTANCE
            if near_x and near_y and random.random() < k.LEDGE_ATTACH_CHANCE * dt:
                self.ledge = ledge
                self.heading = 0.0 if math.cos(self.heading) >= 0 else math.pi
                return

    # -- flight -------------------------------------------------------------

    def start_flight(
        self,
        bounds: tuple[float, float],
        away_from: tuple[float, float] | None = None,
        escape: bool = False,
        effort: float | None = None,
    ) -> None:
        self.state = State.FLYING
        self.ledge = None
        chosen_effort = (
            effort
            if effort is not None
            else (1.0 if escape else random.uniform(*k.FLIGHT_EFFORT_CASUAL))
        )
        self.flight_effort = clamp(chosen_effort, *k.FLIGHT_EFFORT_RANGE)
        self.effort_current = self.flight_effort
        self.flap_phase = 0.0
        self.wing_raise = 0.0
        self.flight_from = (self.x, self.y)

        width, height = bounds
        half_width = width / 2 - k.EDGE_MARGIN
        half_height = height / 2 - k.EDGE_MARGIN
        target = (0.0, 0.0)
        chosen = False

        # A casual hop often aims at a window edge, which is how the fly ends up
        # living on the furniture rather than on the wallpaper.
        if (
            not escape
            and away_from is None
            and self.terrain
            and random.random() < k.FLIGHT_LEDGE_CHANCE
        ):
            ledge = random.choice(self.terrain)
            if ledge.x1 - ledge.x0 > k.FLIGHT_LEDGE_MIN_WIDTH:
                target = (
                    random.uniform(
                        ledge.x0 + k.FLIGHT_LEDGE_INSET, ledge.x1 - k.FLIGHT_LEDGE_INSET
                    ),
                    ledge.y,
                )
                chosen = (
                    math.hypot(target[0] - self.x, target[1] - self.y) > k.FLIGHT_LEDGE_MIN_TRIP
                )

        if not chosen:
            minimum = k.FLIGHT_MIN_DISTANCE_ESCAPE if escape else k.FLIGHT_MIN_DISTANCE_CASUAL
            for _ in range(k.FLIGHT_TARGET_ATTEMPTS):
                target = (
                    random.uniform(-half_width, half_width),
                    random.uniform(-half_height, half_height),
                )
                if math.hypot(target[0] - self.x, target[1] - self.y) <= minimum:
                    continue
                if away_from is not None:
                    # An escape must land on the far side of the fly from the threat.
                    to_target = (target[0] - self.x, target[1] - self.y)
                    to_threat = (away_from[0] - self.x, away_from[1] - self.y)
                    if to_target[0] * to_threat[0] + to_target[1] * to_threat[1] > 0:
                        continue
                break

        self.flight_to = target
        distance = math.hypot(target[0] - self.x, target[1] - self.y)
        speed, minimum_s, maximum_s = (
            k.FLIGHT_DURATION_ESCAPE if escape else k.FLIGHT_DURATION_CASUAL
        )
        self.flight_duration = clamp(distance / speed, minimum_s, maximum_s)
        self.flight_t = 0.0
        self.scare_cooldown = k.SCARE_COOLDOWN_ESCAPE_S if escape else k.SCARE_COOLDOWN_CASUAL_S
        self.model.blur_wing_left.hidden = False
        self.model.blur_wing_right.hidden = False

    def _land(self) -> None:
        self.state = State.IDLE
        self.state_timer = random.uniform(0.3, 0.8)
        self.speed = 0.0
        self.altitude = 0.0
        self.pitch = 0.0
        self.node.scale = [k.FLY_SCALE, k.FLY_SCALE, k.FLY_SCALE]
        self.node.position[2] = 0.0
        for index, wing in enumerate(self.model.folded_wings.children):
            side = -1.0 if index == 0 else 1.0
            wing.euler = [0.0, 0.0, side * 0.13]
        self.model.blur_wing_left.hidden = True
        self.model.blur_wing_right.hidden = True

    def _apply_altitude(self) -> None:
        # Higher means nearer the viewer, so bigger.
        scale = k.FLY_SCALE * (1 + k.ALTITUDE_SCALE_GAIN * self.altitude)
        self.node.scale = [scale, scale, scale]
        self.node.position[2] = k.ALTITUDE_Z * self.altitude

    def _update_flight(self, dt: float) -> None:
        self.flight_t = min(1.0, self.flight_t + dt / self.flight_duration)
        if self.flight_t >= 1.0:
            # Touchdown flare: the timer ran out, but the fly only lands once it
            # has actually descended. Never snap the scale or the height.
            self.x = self.flight_to[0] + math.sin(self.time * 26) * 1.2
            self.y = self.flight_to[1] + math.cos(self.time * 22) * 1.0
            self.pitch = clamp(self.altitude * 0.4, 0.0, k.FLARE_PITCH_LIMIT)
            self.altitude += (0.0 - self.altitude) * min(1.0, k.FLARE_ALTITUDE_LERP * dt)
            self._apply_altitude()
            if self.altitude < k.LANDING_ALTITUDE:
                self.x, self.y = self.flight_to
                self._land()
            return

        eased = smoothstep(self.flight_t)
        dx = self.flight_to[0] - self.flight_from[0]
        dy = self.flight_to[1] - self.flight_from[1]
        length = max(1.0, math.hypot(dx, dy))
        wobble = math.sin(self.time * 32) * 4 * math.sin(self.flight_t * math.pi)
        self.x = self.flight_from[0] + dx * eased + (-dy / length) * wobble
        self.y = self.flight_from[1] + dy * eased + (dx / length) * wobble
        self.heading = math.atan2(dy, dx) + math.sin(self.time * 18) * 0.12

        # Effort stays live: ongoing escape-DN and arousal activity make it beat
        # harder mid-flight. The max() is load-bearing - a live modifier must
        # never weaken a takeoff that already committed.
        if self._brain_live:
            live = (
                self.flight_effort * k.FLIGHT_EFFORT_KEEP
                + self._live_arousal * k.FLIGHT_EFFORT_AROUSAL
                + self._live_wing * k.FLIGHT_EFFORT_WING
            )
            self.effort_current = clamp(
                max(self.flight_effort, live), k.FLIGHT_EFFORT_RANGE[0], k.FLIGHT_EFFORT_LIMIT
            )
        else:
            self.effort_current = self.flight_effort

        rise = min(self.flight_t / k.FLIGHT_RISE_FRACTION, 1.0)
        fall = min((1 - self.flight_t) / k.FLIGHT_FALL_FRACTION, 1.0)
        target = self.effort_current * min(rise, fall) * (0.85 + 0.15 * math.sin(self.time * 7))
        self.pitch = clamp(
            (target - self.altitude) * k.FLIGHT_PITCH_GAIN,
            -k.FLIGHT_PITCH_LIMIT,
            k.FLIGHT_PITCH_LIMIT,
        )
        self.altitude += (target - self.altitude) * min(1.0, k.FLIGHT_ALTITUDE_LERP * dt)
        self._apply_altitude()

    # -- limbs --------------------------------------------------------------

    def _update_legs(self, dt: float) -> None:
        speed = abs(self._effective_speed)
        if self.state is State.WALKING and speed > 1:
            amplitude = clamp(
                k.GAIT_AMPLITUDE[0] + speed * k.GAIT_AMPLITUDE_PER_SPEED, *k.GAIT_AMPLITUDE
            )
            stride = max(5.0, 2 * amplitude * 13)
            frequency = clamp(speed / stride, *k.GAIT_FREQUENCY)
            self.gait_phase = math.fmod(self.gait_phase + frequency * dt, 1.0)
            stance = k.GAIT_STANCE_FRACTION
            for leg in self.model.legs:
                phase = math.fmod(self.gait_phase + leg.phase, 1.0)
                if phase < stance:
                    leg.angle = amplitude * (1 - 2 * (phase / stance))
                    leg.lift = 0.0
                else:
                    swing = (phase - stance) / (1 - stance)
                    leg.angle = -amplitude + 2 * amplitude * smoothstep(swing)
                    leg.lift = math.sin(swing * math.pi) * k.GAIT_LIFT
                if self.backward_timer > 0:
                    leg.angle = -leg.angle
                leg.apply()
        elif self.state is State.GROOMING:
            for leg in self.model.legs:
                if leg.is_front:
                    leg.angle = 0.45 + 0.25 * math.sin(self.time * 20 + leg.swing_sign * 1.3)
                    leg.lift = 0.55 + 0.15 * math.sin(self.time * 22)
                else:
                    leg.angle += (0.0 - leg.angle) * min(1.0, 8 * dt)
                    leg.lift += (0.0 - leg.lift) * min(1.0, 8 * dt)
                leg.apply()
        elif self.state is State.FLYING:
            for leg in self.model.legs:
                leg.angle += (-0.35 - leg.angle) * min(1.0, 6 * dt)
                leg.lift += (0.5 - leg.lift) * min(1.0, 6 * dt)
                leg.apply()
        else:
            for leg in self.model.legs:
                leg.angle += (0.0 - leg.angle) * min(1.0, 10 * dt)
                leg.lift += (0.0 - leg.lift) * min(1.0, 10 * dt)
                leg.apply()

    def _update_wings(self, dt: float) -> None:
        if self.state is not State.FLYING:
            # Grounded threat posture: escape-DN activity raises the wings.
            raising = self.state is not State.SLEEPING and (
                self._live_wing > k.WING_RAISE_THRESHOLD
                or (self._brain_live and self.dart_timer > 0)
            )
            self.wing_raise += ((1.0 if raising else 0.0) - self.wing_raise) * min(
                1.0, k.WING_RAISE_LERP * dt
            )
            if self.wing_raise > 0.01:
                for index, wing in enumerate(self.model.folded_wings.children):
                    side = -1.0 if index == 0 else 1.0
                    wing.euler = [
                        -0.5 * self.wing_raise,
                        0.0,
                        side * (0.13 + 0.3 * self.wing_raise),
                    ]
            return

        self.flap_phase = math.fmod(
            self.flap_phase
            + dt * (k.WING_BEAT_BASE_HZ + k.WING_BEAT_EFFORT_HZ * self.effort_current),
            1.0,
        )
        stroke = math.sin(self.flap_phase * 2 * math.pi)
        for index, wing in enumerate(self.model.folded_wings.children):
            side = -1.0 if index == 0 else 1.0
            wing.euler = [stroke * 0.35, 0.0, side * (0.45 + 0.35 * (0.5 + 0.5 * stroke))]
        flicker = 0.10 + 0.14 * abs(stroke)
        self.model.blur_wing_left.opacity = flicker
        self.model.blur_wing_right.opacity = flicker
        self.model.blur_wing_left.euler = [0.0, 0.0, 0.45 + stroke * 0.2]
        self.model.blur_wing_right.euler = [0.0, 0.0, -0.45 - stroke * 0.2]
