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

import numpy as np

from . import constants as k
from .environment import Ledge
from .geometry import BodyForm, FlyModel, build_body
from .legdynamics import (
    CONTACT_HEIGHT,
    ELEVATION_RANGE,
    HIP_LIMIT,
    REST_KNEE,
    LegBodyMotion,
    LegFeedback,
    LegMotorCommand,
    SixLegDynamics,
    ground_elevation,
)
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


def lag(rate: float, dt: float) -> float:
    """The frame-rate-independent form of the `min(1, k * dt)` idiom.

    Port of FlyModel.swift lag(). Used for both first-order lags and per-frame
    event probabilities, so `rate` keeps its original per-second meaning and
    every call site keeps its original constant. (Upstream calls it `k`; here
    that name already belongs to the constants module.)

    At dt = 1/60 this returns exactly rate/60, the value the constants were
    tuned against, so 60 Hz is unchanged. Away from it the result follows the
    geometric decay those constants already imply rather than a straight line:
    at the 50 ms frame cap the old form converged 27% too fast (0.50 against
    0.39 for rate = 10), which is the visible pop when frames drop.

    Deliberately not `1 - exp(-rate * dt)`. Both are frame-rate independent,
    but the exponential is a different continuous process and would move the
    60 Hz behaviour by 2% at rate = 3 and 8% at rate = 10. This form leaves
    60 Hz alone, and the behaviour suite asserts that it does.
    """
    per_frame = min(1.0, rate / k.TUNED_HZ)
    if per_frame >= 1.0:
        return 1.0
    return float(1.0 - (1.0 - per_frame) ** (k.TUNED_HZ * dt))


class Fly:
    def __init__(self, position: tuple[float, float], form: BodyForm = BodyForm.FLY):
        self.form = form
        self.model: FlyModel = build_body(form)
        self.leg_dynamics = SixLegDynamics([leg.geometry for leg in self.model.legs])
        for leg, pose in zip(self.model.legs, self.leg_dynamics.feedback, strict=True):
            leg.apply_feedback(pose)
        # What the legs are actually doing, read off the displayed pose. Empty
        # until the first frame has sampled it.
        self._sensed_leg_feedback: list[LegFeedback] = []
        self._motor_walking = False
        # Which pose the renderer last showed, so an interrupted transition
        # retargets from what is on screen instead of restarting from rest.
        self._rendered_leg_state: State | None = None
        self._rendered_motor_control = False
        self._leg_blend_from: list[LegFeedback] = []
        self._leg_blend_time = 0.0
        # A heading the body is turning towards, and how long it stays valid.
        self._turn_target: float | None = None
        self._turn_target_time = 0.0
        self._turn_velocity = 0.0
        self._ledge_heading: float | None = None
        self._wing_flight_amount = 0.0
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
        # Radians of body saccade not yet spent, and the rate to spend them at.
        self.saccade = 0.0
        self.saccade_rate = 0.0
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
        self.elytra_open = 0.0  # display only: 0 closed .. 1 fully spread

        self._brain_live = False
        self._live_arousal = 0.0
        self._live_wing = 0.0

        self.sync_node()

    def swap_body(self, form: BodyForm) -> None:
        """Rebuild the geometry in place, keeping every behaviour variable.

        Position, heading, speed, state, gait phase, flight and ledge all
        survive; only the shapes change. Upstream has to reparent the new root
        in its scene graph - here the coordinator hands the renderer whatever
        node the fly currently owns, so there is nothing to re-attach.
        """
        if form is self.form:
            return
        previous = self.node
        self.form = form
        self.model = build_body(form)
        self.leg_dynamics = SixLegDynamics([leg.geometry for leg in self.model.legs])
        for leg, pose in zip(self.model.legs, self.leg_dynamics.feedback, strict=True):
            leg.apply_feedback(pose)
        self._motor_walking = False
        self._rendered_leg_state = None
        self._rendered_motor_control = False
        self._sensed_leg_feedback = []
        self.model.root.position = list(previous.position)
        self.model.root.scale = list(previous.scale)
        self.model.root.euler = list(previous.euler)
        flying = self.state is State.FLYING
        self.model.blur_wing_left.hidden = not flying
        self.model.blur_wing_right.hidden = not flying
        self.sync_node()

    # -- readouts the coordinator feeds back into the brain ------------------

    @property
    def node(self) -> Node:
        return self.model.root

    @property
    def leg_feedback(self) -> list[LegFeedback]:
        """Proprioception as the fly's own body would report it.

        The displayed pose when there is one, so the loop closes on what the
        user actually sees rather than on a physics state the renderer may be
        blending away from.
        """
        if len(self._sensed_leg_feedback) == len(self.model.legs):
            return self._sensed_leg_feedback
        return self.leg_dynamics.feedback

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
        self._turn_target_time = max(0.0, self._turn_target_time - dt)
        if self._turn_target_time == 0:
            self._turn_target = None

        # Live brain drives reach the wings even in mid-flight.
        self._brain_live = signals is not None
        self._live_arousal = signals.arousal if signals else 0.0
        self._live_wing = signals.wing_drive if signals else 0.0

        # Temperature changes how much mechanical time passes, so the forces,
        # the foot contact and the feedback that follows all change together.
        tempo = signals.tempo if signals else 1.0
        motor_tempo = clamp(tempo, *k.MOTOR_TEMPO_LIMITS) if math.isfinite(tempo) else 1.0
        motor_dt = dt * motor_tempo
        commands = signals.leg_commands if signals else None
        driven = commands is not None and len(commands) == len(self.model.legs)

        if self.state is State.FLYING:
            self.saccade = 0.0  # airborne heading is geometric, not a walk saccade
            self._update_flight(dt)
        elif signals is not None:
            if signals.leg_commands is None:
                self._step_saccade(dt)
            self._brain_behavior(signals, dt, bounds, mouse)
            if self.state is State.WALKING:
                if driven:
                    assert commands is not None
                    self._prepare_motor_control(motor_tempo)
                    self.saccade = 0.0
                    motion = self.leg_dynamics.advance(commands, motor_dt)
                    self.speed = abs(motion.forward) / max(0.001, dt)
                    self._update_walk(dt, bounds, motion)
                else:
                    self._motor_walking = False
                    self._update_walk(dt, bounds)
        else:
            self._legacy_behavior(dt, bounds, mouse)

        if not driven or self.state not in (State.WALKING, State.IDLE, State.SLEEPING):
            self._motor_walking = False
        if driven and self.state in (State.IDLE, State.SLEEPING):
            # Standing is a posture the legs hold, not a pose they snap to.
            self._prepare_motor_control(motor_tempo)
            self.leg_dynamics.advance(
                [LegMotorCommand() for _ in self.model.legs], motor_dt
            )
            self._motor_walking = True
        if not self._motor_walking:
            self.leg_dynamics.reset_contact(grounded=self.state is not State.FLYING)

        self._update_legs(dt)
        self._sample_leg_feedback(dt)
        self._update_wings(dt)
        rate, depth = k.BREATHE_ASLEEP if self.state is State.SLEEPING else k.BREATHE_AWAKE
        self.model.abdomen.scale = [0.9, 1.5, 0.75 * (1 + depth * math.sin(self.time * rate))]
        self.sync_node()

    def sync_node(self) -> None:
        node = self.node
        node.position = [self.x, self.y, node.position[2]]
        node.euler = [self.pitch, 0.0, self.heading - math.pi / 2]

    def _prepare_motor_control(self, tempo: float) -> None:
        """Hand the displayed pose to the physics, once, on the way in.

        Adopting the pose rather than resetting to one is the whole point: the
        legs carry on from where the renderer had them, so no behaviour change
        shows up as a joint snapping back to rest.
        """
        if not self._motor_walking:
            self.leg_dynamics.adopt_pose(
                self.leg_feedback, grounded=True, velocity_scale=1.0 / tempo
            )
        self._motor_walking = True

    def _sample_leg_feedback(self, dt: float) -> None:
        """Read proprioception off the rendered skeleton.

        Both control paths agree on the result because the toe position comes
        from the same node chain the renderer draws, not from a physics state
        that a blend may still be catching up with.
        """
        previous = self.leg_feedback
        physical = self.leg_dynamics.feedback
        sensed: list[LegFeedback] = []
        for index, leg in enumerate(self.model.legs):
            chain = leg.root.local_matrix() @ leg.knee.local_matrix() @ leg.ankle.local_matrix()
            toe = chain @ np.array([leg.geometry.tarsus, 0.0, 0.0, 1.0], dtype=np.float32)
            value = LegFeedback(
                hip_angle=leg.angle,
                knee_angle=leg.knee_angle,
                elevation_angle=leg.lift,
                foot_x=float(toe[0]),
                foot_y=float(toe[1]),
                foot_height=float(toe[2]) + self.node.position[2],
            )
            span = max(0.001, dt)
            value.hip_velocity = (value.hip_angle - previous[index].hip_angle) / span
            value.knee_velocity = (value.knee_angle - previous[index].knee_angle) / span
            value.elevation_velocity = (
                value.elevation_angle - previous[index].elevation_angle
            ) / span
            value.contact = self.state is not State.FLYING and value.foot_height <= CONTACT_HEIGHT
            sensed.append(value)
        supports = max(1, sum(1 for value in sensed if value.contact))
        for index, value in enumerate(sensed):
            if not value.contact:
                value.load = 0.0
            elif self._motor_walking:
                value.load = physical[index].load
            else:
                value.load = 1.0 / supports
        self._sensed_leg_feedback = sensed

    def _set_state(self, state: State) -> None:
        if state is self.state:
            return
        self.state = state
        self.state_age = 0.0
        if state is not State.WALKING:
            self._turn_target = None

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
                self.saccade = 0.0
                self._turn_target = math.atan2(
                    self.y - mouse[1], self.x - mouse[0]
                ) + random.uniform(-0.4, 0.4)
            else:
                self._start_saccade()
            self.speed = random.uniform(*k.DART_SPEED)
            self.dart_timer = random.uniform(*k.DART_DURATION_S)
            self._turn_target_time = self.dart_timer
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
            self._start_saccade()
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

        # The two lines below are what the body did before the nerve cord: a
        # commanded speed and a commanded turn. With real motor output present
        # they must not run, or a silenced cord would still walk the fly.
        if self.state is State.WALKING:
            if s.leg_commands is None and self.dart_timer == 0 and self.backward_timer == 0:
                target = (k.WALK_SPEED_BASE + s.walk_drive * k.WALK_SPEED_GAIN) * s.tempo
                self.speed += (target - self.speed) * lag(k.WALK_SPEED_LERP, dt)
            if s.leg_commands is None and self.ledge is None:
                self.heading += s.turn_bias * dt  # DNa01/DNa02 steering

        # Spontaneous takeoff, gated on whole-population arousal; how aroused the
        # network is also sets how hard it beats and therefore how high it goes.
        chance = (
            k.FLIGHT_CHANCE_AROUSED if s.arousal > k.FLIGHT_AROUSAL_GATE else k.FLIGHT_CHANCE_CALM
        )
        if self.state is State.WALKING and random.random() < lag(chance, dt):
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
                self.saccade = 0.0
                self._turn_target = math.atan2(
                    self.y - mouse[1], self.x - mouse[0]
                ) + random.uniform(-0.4, 0.4)
                self.speed = random.uniform(110.0, 150.0)
                self.state_timer = random.uniform(0.4, 0.9)
                self._turn_target_time = self.state_timer
                self.scare_cooldown = 1.0
        if self.state is State.FLYING:
            return
        self._step_saccade(dt)
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
                self._start_saccade()
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
                self._start_saccade()
        elif self.state is State.GROOMING:
            self.state = State.IDLE
            self.state_timer = random.uniform(0.3, 1.0)

    # -- walking ------------------------------------------------------------

    def _update_walk(
        self, dt: float, bounds: tuple[float, float], motor_motion: LegBodyMotion | None = None
    ) -> None:
        width, height = bounds
        if self.ledge is not None and not self._refresh_ledge(bounds):
            return

        if self.ledge is not None:
            ledge = self.ledge
            # Walking an edge means holding a heading of 0 or pi and tracking
            # the edge height; the window may be moving under its feet.
            if motor_motion is None:
                self.heading += random.uniform(-1.0, 1.0) * k.LEDGE_JITTER * math.sqrt(dt)
            # Which way along the edge it is going is latched and turned into,
            # rather than recomputed and assigned: reaching the end used to flip
            # the heading by pi within one tick.
            if self._ledge_heading is None:
                self._ledge_heading = 0.0 if math.cos(self.heading) >= 0 else math.pi
            if self.x <= ledge.x0 + k.LEDGE_END_MARGIN:
                self._ledge_heading = 0.0
            if self.x >= ledge.x1 - k.LEDGE_END_MARGIN:
                self._ledge_heading = math.pi
            self._turn_toward(self._ledge_heading, dt)
            along_edge = (
                motor_motion.forward if motor_motion is not None else self._effective_speed * dt
            )
            self.x += math.cos(self.heading) * along_edge
            self.y += (ledge.y - self.y) * lag(k.LEDGE_SNAP_LERP, dt)
            self.x = clamp(self.x, ledge.x0, ledge.x1)
            if random.random() < lag(k.LEDGE_LEAVE_CHANCE, dt):
                self.ledge = None
        else:
            self._ledge_heading = None
            if self._turn_target is not None:
                self._turn_toward(self._turn_target, dt)
                if abs(angle_difference(self.heading, self._turn_target)) < 0.001:
                    self._turn_target = None
            start_heading = self.heading
            if motor_motion is not None:
                self.heading += motor_motion.yaw
            else:
                self.heading += random.uniform(-1.0, 1.0) * k.WANDER_JITTER * math.sqrt(dt)
            half_width = width / 2 - k.EDGE_MARGIN
            half_height = height / 2 - k.EDGE_MARGIN
            if abs(self.x) > half_width or abs(self.y) > half_height:
                to_center = math.atan2(-self.y, -self.x)
                self.heading += angle_difference(self.heading, to_center) * lag(
                    k.BOUNDARY_STEER_LERP, dt
                )
            if motor_motion is not None:
                forward, lateral = motor_motion.forward, motor_motion.lateral
            else:
                forward, lateral = self._effective_speed * dt, 0.0
            # The mechanics integrates displacement in the frame the tick began
            # in, so rotating it by the final heading would apply the turn twice.
            heading = start_heading if motor_motion is not None else self.heading
            self.x += math.cos(heading) * forward + math.sin(heading) * lateral
            self.y += math.sin(heading) * forward - math.cos(heading) * lateral
            self.x = clamp(self.x, -width / 2 + k.WALL_MARGIN, width / 2 - k.WALL_MARGIN)
            self.y = clamp(self.y, -height / 2 + k.WALL_MARGIN, height / 2 - k.WALL_MARGIN)
            self._maybe_attach_ledge(dt)

        # Under motor control the legs supply the body height themselves.
        self.node.position[2] = (
            0.0
            if motor_motion is not None
            else k.GAIT_BOB_Z * abs(math.sin(self.gait_phase * math.pi * 2))
        )

    def _refresh_ledge(self, bounds: tuple[float, float]) -> bool:
        """Follow the window this edge belongs to; take off if it vanished.

        Returns False when the fly left the ground, so the caller stops walking.

        The fly has to still be over the edge for it to count as the same
        ground. Matching on height alone re-latched onto a window dragged
        sideways, and the clamp below then teleported the fly across the screen
        to catch up with it.
        """
        assert self.ledge is not None
        current = next((L for L in self.terrain if L.key == self.ledge.key), None)
        if (
            current is not None
            and abs(current.y - self.ledge.y) < k.LEDGE_LOST_DISTANCE
            and current.x0 - k.LEDGE_END_MARGIN <= self.x <= current.x1 + k.LEDGE_END_MARGIN
        ):
            self.ledge = current
            return True
        self.ledge = None
        self.start_flight(bounds)  # the ground vanished from under its feet
        return False

    def _maybe_attach_ledge(self, dt: float) -> None:
        for ledge in self.terrain:
            near_x = ledge.x0 - 8 < self.x < ledge.x1 + 8
            near_y = abs(self.y - ledge.y) < k.LEDGE_ATTACH_DISTANCE
            if near_x and near_y and random.random() < lag(k.LEDGE_ATTACH_CHANCE, dt):
                self.ledge = ledge
                self._ledge_heading = 0.0 if math.cos(self.heading) >= 0 else math.pi
                return

    # -- flight -------------------------------------------------------------

    def start_flight(
        self,
        bounds: tuple[float, float],
        away_from: tuple[float, float] | None = None,
        escape: bool = False,
        effort: float | None = None,
    ) -> None:
        self._set_state(State.FLYING)
        self.ledge = None
        self._ledge_heading = None
        self._turn_target = None
        self.saccade = 0.0
        chosen_effort = (
            effort
            if effort is not None
            else (1.0 if escape else random.uniform(*k.FLIGHT_EFFORT_CASUAL))
        )
        self.flight_effort = clamp(chosen_effort, *k.FLIGHT_EFFORT_RANGE)
        self.effort_current = self.flight_effort
        # The stroke and the threat posture carry on from wherever they were.
        # Zeroing them here made the wings jump at every takeoff.
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

    def _turn_toward(self, target: float, dt: float) -> None:
        """Turn the body towards a heading at a bounded rate.

        Fast reactions used to assign the heading outright, which rotated the
        whole animal inside one tick. This still gets there quickly - the gain
        is high - but it gets there by turning.
        """
        error = angle_difference(self.heading, target)
        desired = clamp(error * k.TURN_GAIN, -k.TURN_RATE_LIMIT, k.TURN_RATE_LIMIT)
        self._turn_velocity += clamp(
            desired - self._turn_velocity,
            -k.TURN_ACCELERATION_LIMIT * dt,
            k.TURN_ACCELERATION_LIMIT * dt,
        )
        step = self._turn_velocity * dt
        if step * error >= 0 and abs(step) >= abs(error):
            self.heading += error
            self._turn_velocity = 0.0
        else:
            self.heading += step

    def _start_saccade(self) -> None:
        """Queue a small spontaneous body saccade.

        Larger changes of direction go through _turn_toward instead, so a fast
        reaction does not rotate the entire animal in one tick. The escape
        reflex itself stays quick either way: a fleeing fly extends its legs in
        3.33 ms (Card & Dickinson 2008, J Exp Biol 211:341).
        """
        sign = -1.0 if random.random() < 0.5 else 1.0
        self.saccade = sign * random.uniform(*k.SACCADE_AMPLITUDE)
        self.saccade_rate = self.saccade / k.SACCADE_DURATION_S

    def _step_saccade(self, dt: float) -> None:
        if self.saccade == 0.0:
            return
        step = self.saccade_rate * dt
        if abs(step) >= abs(self.saccade):
            self.heading += self.saccade
            self.saccade = 0.0
        else:
            self.heading += step
            self.saccade -= step

    def _land(self) -> None:
        self._set_state(State.IDLE)
        self.state_timer = random.uniform(0.3, 0.8)
        self.speed = 0.0
        self.altitude = 0.0
        self.pitch = 0.0
        self.node.scale = [k.FLY_SCALE, k.FLY_SCALE, k.FLY_SCALE]
        self.node.position[2] = 0.0
        # Wing closure and leg settling continue from their airborne poses.

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
            settle = min(1.0, self.altitude / k.FLARE_SETTLE_ALTITUDE)
            self.x = self.flight_to[0] + math.sin(self.time * 26) * 1.2 * settle
            self.y = self.flight_to[1] + math.cos(self.time * 22) * settle
            self.pitch += (
                clamp(self.altitude * 0.4, 0.0, k.FLARE_PITCH_LIMIT) - self.pitch
            ) * lag(k.PITCH_LERP, dt)
            self.altitude += (0.0 - self.altitude) * lag(k.FLARE_ALTITUDE_LERP, dt)
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
        self._turn_toward(math.atan2(dy, dx) + math.sin(self.time * 18) * 0.12, dt)

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
        self.pitch += (
            clamp(
                (target - self.altitude) * k.FLIGHT_PITCH_GAIN,
                -k.FLIGHT_PITCH_LIMIT,
                k.FLIGHT_PITCH_LIMIT,
            )
            - self.pitch
        ) * lag(k.PITCH_LERP, dt)
        self.altitude += (target - self.altitude) * lag(k.FLIGHT_ALTITUDE_LERP, dt)
        self._apply_altitude()

    # -- limbs --------------------------------------------------------------

    def _update_legs(self, dt: float) -> None:
        if self._motor_walking:
            for leg, feedback in zip(self.model.legs, self.leg_dynamics.feedback, strict=True):
                leg.apply_feedback(feedback)
            self._rendered_leg_state = self.state
            self._rendered_motor_control = True
            return

        # Retarget from the pose on screen, including when one transition
        # interrupts another. Under motor control the physics adopts that same
        # pose instead, which is what keeps the two paths agreeing.
        if self._rendered_leg_state is not self.state or self._rendered_motor_control:
            self._leg_blend_from = [leg.pose() for leg in self.model.legs]
            self._leg_blend_time = 0.0
        self._rendered_leg_state = self.state
        self._rendered_motor_control = False
        self._leg_blend_time = min(k.LEG_BLEND_S, self._leg_blend_time + dt)
        blend = smoothstep(self._leg_blend_time / k.LEG_BLEND_S)

        speed = abs(self._effective_speed)
        walking = self.state is State.WALKING and speed > 1
        amplitude = clamp(
            k.GAIT_AMPLITUDE[0] + speed * k.GAIT_AMPLITUDE_PER_SPEED, *k.GAIT_AMPLITUDE
        )
        stride = max(5.0, 2 * amplitude * 13)
        frequency = clamp(speed / stride, *k.GAIT_FREQUENCY)
        if walking:
            self.gait_phase = math.fmod(self.gait_phase + frequency * dt, 1.0)
        stance = clamp(1 - k.SWING_DURATION_S * frequency, *k.GAIT_STANCE_LIMITS)

        for index, leg in enumerate(self.model.legs):
            angle, lift, knee = 0.0, 0.0, REST_KNEE
            if walking:
                knee = k.GAIT_KNEE_ANGLE
                phase = math.fmod(self.gait_phase + leg.phase, 1.0)
                if phase < stance:
                    angle = amplitude * (1 - 2 * (phase / stance))
                else:
                    swing = (phase - stance) / (1 - stance)
                    angle = -amplitude + 2 * amplitude * smoothstep(swing)
                    lift = math.sin(swing * math.pi) * k.GAIT_LIFT
                if self.backward_timer > 0:
                    angle = -angle
            elif self.state is State.GROOMING:
                knee = k.GAIT_KNEE_ANGLE
                if leg.is_front:
                    angle = 0.45 + 0.25 * math.sin(self.time * 20 + leg.swing_sign * 1.3)
                    lift = 0.55 + 0.15 * math.sin(self.time * 22)
            elif self.state is State.FLYING:
                angle, lift, knee = -0.35, 0.5, k.GAIT_KNEE_ANGLE

            angle = clamp(angle, -HIP_LIMIT, HIP_LIMIT)
            lift = clamp(lift, *ELEVATION_RANGE)
            # A scripted pose is held to the same ground the mechanics uses, so
            # a later handover needs no projection and no foot ever sinks.
            if self.state is not State.FLYING:
                lift = max(lift, ground_elevation(leg.geometry, knee))

            source = self._leg_blend_from[index]
            leg.angle = source.hip_angle + (angle - source.hip_angle) * blend
            leg.knee_angle = source.knee_angle + (knee - source.knee_angle) * blend
            leg.lift = source.elevation_angle + (lift - source.elevation_angle) * blend
            if self.state is not State.FLYING:
                leg.lift = max(leg.lift, ground_elevation(leg.geometry, leg.knee_angle))
            leg.apply()

    def _update_wings(self, dt: float) -> None:
        """One continuous path for folded, raised and beating wings.

        Opening and closing are eased and the stroke is gated behind them, so
        the wings spread before the first downstroke and flatten before folding.
        Beating through a half-folded wing swept it through the thorax.
        """
        flying = self.state is State.FLYING
        self._wing_flight_amount += ((1.0 if flying else 0.0) - self._wing_flight_amount) * lag(
            k.WING_FLIGHT_LERP, dt
        )
        if not flying and self._wing_flight_amount < 0.0001:
            self._wing_flight_amount = 0.0

        # Grounded threat posture: escape-DN activity raises the wings.
        raising = (
            not flying
            and self.state is not State.SLEEPING
            and (
                self._live_wing > k.WING_RAISE_THRESHOLD
                or (self._brain_live and self.dart_timer > 0)
            )
        )
        self.wing_raise += ((1.0 if raising else 0.0) - self.wing_raise) * lag(
            k.WING_RAISE_LERP, dt
        )

        if flying or self._wing_flight_amount > 0:
            self.flap_phase += dt * (
                k.WING_BEAT_BASE_HZ + k.WING_BEAT_EFFORT_HZ * self.effort_current
            )
        stroke = math.sin(self.flap_phase * 2 * math.pi)
        gate_start, gate_width = k.WING_BEAT_GATE
        beat = smoothstep((self._wing_flight_amount - gate_start) / gate_width)

        amount = self._wing_flight_amount
        grounded_spread = 0.13 + 0.3 * self.wing_raise
        spread = grounded_spread + (self.model.wing_flight_spread - grounded_spread) * amount
        for index, wing in enumerate(self.model.folded_wings.children):
            side = -1.0 if index == 0 else 1.0
            wing.euler = [
                -0.5 * self.wing_raise * (1 - amount) + stroke * 0.35 * beat,
                0.0,
                side * (spread + k.WING_STROKE_ROLL * stroke * beat),
            ]

        flicker = (0.10 + 0.14 * abs(stroke)) * amount
        self.model.blur_wing_left.opacity = flicker
        self.model.blur_wing_right.opacity = flicker
        self.model.blur_wing_left.hidden = amount == 0.0
        self.model.blur_wing_right.hidden = amount == 0.0
        self.model.blur_wing_left.euler = [0.0, 0.0, 0.45 + stroke * 0.2]
        self.model.blur_wing_right.euler = [0.0, 0.0, -0.45 - stroke * 0.2]
        self._update_elytra(1.0 if flying else self.wing_raise, dt)

    def _update_elytra(self, target: float, dt: float) -> None:
        """Display only, and only on a body that has wing cases.

        It reads `state is FLYING` and the existing wing_raise; it adds no
        signal and makes no decision. The cases swing outward and tip up, then
        hold that angle instead of buzzing along with the hindwings.
        """
        left, right = self.model.elytra_left, self.model.elytra_right
        if left is None or right is None:
            return
        self.elytra_open += (target - self.elytra_open) * lag(k.ELYTRA_LERP, dt)
        if self.elytra_open < 0.001:
            self.elytra_open = 0.0
        yaw = k.ELYTRA_YAW * self.elytra_open
        lift = k.ELYTRA_LIFT * self.elytra_open
        left.euler = [0.0, lift, -yaw]
        right.euler = [0.0, -lift, yaw]
