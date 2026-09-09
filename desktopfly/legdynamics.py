"""Articulated leg mechanics: joints, foot contact, and the body motion they cause.

Port of LegDynamics.swift. This is a reduced articulated body, NOT measured fly
physics. The motor channels correspond to annotated muscle actions, but the
inertia, the damping, the joint limits and the no-slip ground approximation
below are explicit modelling choices - see data/LOCOMOTOR_PROVENANCE.md.

Nothing here knows about gait phase, body speed or a desired foot trajectory.
Given six antagonist commands it integrates six legs and reports what actually
moved; the walking has to fall out of the circuit driving those commands.

The joint limits are module constants because behavior.py and locomotor.py both
read them by upstream's names. The per-joint force, damping and stiffness
triples stay inline in step(): like the body dimensions in geometry.py they
describe one specific model of a joint rather than a knob anybody should turn,
and they only mean anything next to each other in the expression they form.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

# Joint limits, radians. Upstream's names, because they are read from outside.
HIP_LIMIT = 0.65
KNEE_RANGE = (0.15, 1.80)
ELEVATION_RANGE = (-0.25, 1.35)
REST_KNEE = 0.95
ANKLE_ANGLE = 0.35  # fixed: the model gives the tarsus no actuator

# A toe this close to the substrate counts as touching it.
CONTACT_HEIGHT = 0.015

# Mechanics runs far finer than the loop that drives it, because the ground
# constraint is resolved per substep and a coarse step lets a toe sink visibly
# before the substrate pushes back.
MECHANICS_STEP_S = 1.0 / 600.0
MECHANICS_MAX_CATCHUP_S = 0.1

# Body displacement per substep is clamped, so a numerical spike in the support
# fit cannot teleport the fly. Expressed per second and scaled by the substep.
BODY_YAW_LIMIT_PER_S = 5.0
BODY_TRANSLATION_LIMIT_PER_S = 150.0

# Ceilings on velocities adopted from a scripted pose, so a rendered animation
# that moved a joint quickly cannot inject an impossible one into the physics.
ADOPT_HIP_VELOCITY_LIMIT = 20.0
ADOPT_ELEVATION_VELOCITY_LIMIT = 20.0
ADOPT_KNEE_VELOCITY_LIMIT = 40.0


def limited(value: float, low: float, high: float) -> float:
    """Clamp, treating a non-finite value as the low bound rather than spreading it."""
    return min(high, max(low, value)) if math.isfinite(value) else low


@dataclass
class LegMotorCommand:
    """Six antagonist activations, 0..1. Port of LegDynamics.swift LegMotorCommand."""

    protract: float = 0.0
    retract: float = 0.0
    lift: float = 0.0
    depress: float = 0.0
    flex: float = 0.0
    extend: float = 0.0


@dataclass
class LegFeedback:
    """What one leg is doing, and what its sensors would report."""

    hip_angle: float = 0.0
    hip_velocity: float = 0.0
    knee_angle: float = REST_KNEE
    knee_velocity: float = 0.0
    contact: bool = False
    load: float = 0.0
    foot_height: float = 0.0
    elevation_angle: float = 0.0
    elevation_velocity: float = 0.0
    foot_x: float = 0.0
    foot_y: float = 0.0


@dataclass(frozen=True)
class LegGeometry:
    """Where a leg is attached and how long its segments are."""

    attach_x: float
    attach_y: float
    attach_z: float
    base_yaw: float
    side: float
    femur: float
    tibia: float
    tarsus: float


@dataclass
class LegBodyMotion:
    """Rigid-body displacement in the frame the update started in."""

    forward: float = 0.0
    lateral: float = 0.0
    yaw: float = 0.0


def ground_elevation(geometry: LegGeometry, knee: float) -> float:
    """The elevation angle that puts this leg's toe exactly on z = 0.

    Solved from the body's own segment lengths, so swapping to another body
    keeps its feet on its own ground rather than on the fruit fly's.
    """
    a = (
        geometry.femur
        + geometry.tibia * math.cos(knee)
        + geometry.tarsus * math.cos(knee + ANKLE_ANGLE)
    )
    b = geometry.tibia * math.sin(knee) + geometry.tarsus * math.sin(knee + ANKLE_ANGLE)
    reach = max(0.001, math.hypot(a, b))
    return math.atan2(b, a) - math.asin(limited(geometry.attach_z / reach, -1.0, 1.0))


class LegDynamics:
    """One leg: three driven joints and a toe that cannot go through the floor."""

    def __init__(self, geometry: LegGeometry):
        self.geometry = geometry
        self._rest_elevation = ground_elevation(geometry, REST_KNEE)
        self.feedback = LegFeedback(elevation_angle=self._rest_elevation)
        self._update_foot(grounded=True)

    def _update_foot(self, grounded: bool) -> None:
        g, f = self.geometry, self.feedback
        elevation, knee = f.elevation_angle, f.knee_angle
        reach = (
            g.femur * math.cos(elevation)
            + g.tibia * math.cos(elevation - knee)
            + g.tarsus * math.cos(elevation - knee - ANKLE_ANGLE)
        )
        yaw = g.base_yaw + g.side * f.hip_angle
        f.foot_x = g.attach_x + math.cos(yaw) * reach
        f.foot_y = g.attach_y + math.sin(yaw) * reach
        f.foot_height = (
            g.attach_z
            + g.femur * math.sin(elevation)
            + g.tibia * math.sin(elevation - knee)
            + g.tarsus * math.sin(elevation - knee - ANKLE_ANGLE)
        )
        f.contact = grounded and f.foot_height <= CONTACT_HEIGHT
        f.load = 1.0 if f.contact else 0.0

    def reset_contact(self, grounded: bool) -> None:
        self._update_foot(grounded)

    def adopt_pose(self, pose: LegFeedback, grounded: bool, velocity_scale: float = 1.0) -> None:
        """Take control back from a scripted pose without restoring an old walking state.

        The incoming velocities are observed movement, not remembered motor
        velocities, so they are divided by the thermal tempo when wall time is
        converted to model time.
        """
        angles = (pose.hip_angle, pose.elevation_angle, pose.knee_angle)
        if not all(math.isfinite(a) for a in angles):
            return
        scale = velocity_scale if math.isfinite(velocity_scale) and velocity_scale > 0 else 1.0

        def speed(value: float, limit: float) -> float:
            scaled = value * scale
            return limited(scaled, -limit, limit) if math.isfinite(scaled) else 0.0

        f = self.feedback
        f.hip_angle = limited(pose.hip_angle, -HIP_LIMIT, HIP_LIMIT)
        f.elevation_angle = limited(pose.elevation_angle, *ELEVATION_RANGE)
        f.knee_angle = limited(pose.knee_angle, *KNEE_RANGE)
        f.hip_velocity = speed(pose.hip_velocity, ADOPT_HIP_VELOCITY_LIMIT)
        f.elevation_velocity = speed(pose.elevation_velocity, ADOPT_ELEVATION_VELOCITY_LIMIT)
        f.knee_velocity = speed(pose.knee_velocity, ADOPT_KNEE_VELOCITY_LIMIT)
        # Keep the incoming pose even at touchdown. Ground constraints are
        # resolved by the next physical step, never by a teleport during adoption.
        self._update_foot(grounded)
        f.load = 0.0

    def step(self, command: LegMotorCommand, dt: float, grounded: bool) -> None:
        """Semi-implicit damped joint integration.

        An antagonist difference supplies torque; coactivating both sides adds
        stiffness rather than generating free movement.
        """
        protract = limited(command.protract, 0.0, 1.0)
        retract = limited(command.retract, 0.0, 1.0)
        lift = limited(command.lift, 0.0, 1.0)
        depress = limited(command.depress, 0.0, 1.0)
        flex = limited(command.flex, 0.0, 1.0)
        extend = limited(command.extend, 0.0, 1.0)

        f = self.feedback
        old = replace(f)

        f.hip_velocity += (
            300 * (protract - retract)
            - 26 * old.hip_velocity
            - (18 + 12 * min(protract, retract)) * old.hip_angle
        ) * dt
        f.hip_angle = limited(old.hip_angle + f.hip_velocity * dt, -HIP_LIMIT, HIP_LIMIT)

        # A small modelled body load keeps unpowered supporting legs on the
        # ground; the substrate reaction below counters it without translating
        # the fly.
        f.elevation_velocity += (
            800 * (lift - depress)
            - 45 * old.elevation_velocity
            - (800 + 120 * min(lift, depress)) * (old.elevation_angle - self._rest_elevation)
            - (80 if grounded else 0)
        ) * dt
        f.elevation_angle = limited(
            old.elevation_angle + f.elevation_velocity * dt, *ELEVATION_RANGE
        )

        # Shared muscle and joint calibration: the tibia must respond before a
        # coactivated trochanter unloads it. A weaker, slower knee reversed the
        # MDN power stroke by flexing only once the foot was already airborne.
        f.knee_velocity += (
            1140 * (flex - extend)
            - 36 * old.knee_velocity
            - (240 + 80 * min(flex, extend)) * (old.knee_angle - REST_KNEE)
        ) * dt
        f.knee_angle = limited(old.knee_angle + f.knee_velocity * dt, *KNEE_RANGE)

        self._update_foot(grounded)
        attempted_elevation = f.elevation_angle
        reaction = 0.0
        if grounded and f.foot_height < 0:
            # Unilateral ground constraint: the substrate can push a foot up,
            # never pull it down, so elevation is projected only where the toe
            # actually penetrates.
            f.elevation_angle = max(
                f.elevation_angle, ground_elevation(self.geometry, f.knee_angle)
            )
            # The acceleration the constraint removed estimates the relative
            # support reaction. Lift reduces it before toe-off and depression
            # raises it. Model units, not newtons.
            reaction = max(0.0, f.elevation_angle - attempted_elevation) / (dt * dt)
            self._update_foot(grounded)
        f.load = reaction if f.contact else 0.0

        # Report the movement that actually happened, after joint limits and
        # contact - not the attempted motor velocity. This is the proprioceptive
        # signal that goes back to the nerve cord.
        f.hip_velocity = (f.hip_angle - old.hip_angle) / dt
        f.elevation_velocity = (f.elevation_angle - old.elevation_angle) / dt
        f.knee_velocity = (f.knee_angle - old.knee_angle) / dt


class SixLegDynamics:
    """The six legs together, and the body motion their feet drag out of the ground."""

    def __init__(self, geometries: list[LegGeometry]):
        self.legs = [LegDynamics(geometry) for geometry in geometries]
        self._accumulator = 0.0

    @property
    def feedback(self) -> list[LegFeedback]:
        """Per-leg state with load normalised to a share of total body support."""
        total = sum(leg.feedback.load for leg in self.legs if leg.feedback.contact)
        result = []
        for leg in self.legs:
            value = replace(leg.feedback)
            value.load = value.load / total if value.contact and total > 0 else 0.0
            result.append(value)
        return result

    def reset_contact(self, grounded: bool) -> None:
        for leg in self.legs:
            leg.reset_contact(grounded)

    def adopt_pose(
        self, poses: list[LegFeedback], grounded: bool, velocity_scale: float = 1.0
    ) -> None:
        """Hand a whole rendered pose to the physics, or none of it.

        An incomplete or invalid sample is rejected atomically rather than
        combining some newly rendered joints with stale ones from the previous
        mode.
        """
        if len(poses) != len(self.legs):
            return
        for pose in poses:
            if not all(
                math.isfinite(a) for a in (pose.hip_angle, pose.elevation_angle, pose.knee_angle)
            ):
                return
        for leg, pose in zip(self.legs, poses, strict=True):
            leg.adopt_pose(pose, grounded, velocity_scale)
        # A leftover substep or an old support reaction must not become a
        # phantom stance stroke when the next neural command arrives.
        self._accumulator = 0.0

    def advance(
        self, commands: list[LegMotorCommand], dt: float, grounded: bool = True
    ) -> LegBodyMotion:
        if len(commands) != len(self.legs) or not math.isfinite(dt) or dt <= 0:
            return LegBodyMotion()
        self._accumulator += min(dt, MECHANICS_MAX_CATCHUP_S)
        result = LegBodyMotion()
        h = MECHANICS_STEP_S
        while self._accumulator + 1e-10 >= h:
            self._accumulator -= h
            old = self.feedback
            for leg, command in zip(self.legs, commands, strict=True):
                leg.step(command, h, grounded)
            current = self.feedback
            if not grounded:
                continue
            # Fit the rigid-body displacement that keeps the supporting feet
            # still on the ground. A foot that has just landed or just left
            # supplies no thrust.
            support = [
                i
                for i in range(len(self.legs))
                if old[i].contact
                and current[i].contact
                and old[i].load > 1e-8
                and current[i].load > 1e-8
            ]
            if len(support) < 2:
                continue
            weights = [math.sqrt(old[i].load * current[i].load) for i in support]
            total_weight = sum(weights)
            mx = my = dx = dy = 0.0
            for weight, i in zip(weights, support, strict=True):
                share = weight / total_weight
                mx += current[i].foot_x * share
                my += current[i].foot_y * share
                dx += (current[i].foot_x - old[i].foot_x) * share
                dy += (current[i].foot_y - old[i].foot_y) * share
            moment = radius = 0.0
            for weight, i in zip(weights, support, strict=True):
                share = weight / total_weight
                x = current[i].foot_x - mx
                y = current[i].foot_y - my
                moment += share * (
                    x * (current[i].foot_y - old[i].foot_y - dy)
                    - y * (current[i].foot_x - old[i].foot_x - dx)
                )
                radius += share * (x * x + y * y)
            yaw = limited(
                -moment / max(1.0, radius), -BODY_YAW_LIMIT_PER_S * h, BODY_YAW_LIMIT_PER_S * h
            )
            translation_limit = BODY_TRANSLATION_LIMIT_PER_S * h
            lateral = limited(-dx + yaw * my, -translation_limit, translation_limit)
            forward = limited(-dy - yaw * mx, -translation_limit, translation_limit)
            # Rotate each substep into the frame this whole update started in.
            result.lateral += lateral * math.cos(result.yaw) - forward * math.sin(result.yaw)
            result.forward += lateral * math.sin(result.yaw) + forward * math.cos(result.yaw)
            result.yaw += yaw
        return result
