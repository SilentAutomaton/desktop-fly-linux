"""Causal checks on the MaleCNS circuit, the leg mechanics and the loop between them.

Port of LocomotorTests.swift. These check software invariants, not fidelity to
measured animal physiology: a successful path validates the extraction and the
integration, never the biology. A short motion transient is not evidence of
sustained walking, which is why most checks measure a window that starts after
the model has settled.

Several checks are lesions - silencing the motor pool, cutting the synapses,
opening the feedback loop - because the only way to show that a movement
travelled the causal path it claims to is to remove that path and watch the
movement stop.
"""

from __future__ import annotations

import copy
import math
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from desktopfly import constants as k
from desktopfly.behavior import Fly, State, angle_difference
from desktopfly.dataset import LEG_COUNT, BrainData, load_brain_data
from desktopfly.environment import Ledge
from desktopfly.geometry import build_fly_model
from desktopfly.legdynamics import LegMotorCommand, SixLegDynamics
from desktopfly.locomotor import LocomotorSim
from desktopfly.scenegraph import Mat3
from desktopfly.selftest import DEFAULT_SEED
from desktopfly.signals import SignalBuilder
from desktopfly.sim import BrainSignals, LIFSim

BOUNDS = (1512.0, 982.0)
TICK = k.SIMULATION_TICK_S
SETTLED_S = 3.0  # everything before this is the model getting going

# Per-tick ceilings for the transition fixtures. Before the easing went in, a
# state change moved a toe 11.4 units and flipped the heading by pi in one tick.
JOINT_JUMP_LIMIT = 0.35  # rad
TOE_JUMP_LIMIT = 3.0  # units
HEADING_JUMP_LIMIT = 0.18  # rad
PITCH_JUMP_LIMIT = 0.08  # rad
WARMUP_TICKS = 360  # of real motor walking, never a reset pose


@dataclass
class LocomotorTrial:
    """What one open-loop run of the cord and the mechanics did."""

    forward: float = 0.0
    lateral: float = 0.0
    yaw: float = 0.0
    late_distance: float = 0.0  # path length once settled
    late_forward: float = 0.0
    late_yaw: float = 0.0
    contacts: list[int] = field(default_factory=lambda: [0] * LEG_COUNT)
    ranges: list[float] = field(default_factory=lambda: [0.0] * LEG_COUNT)
    motor_rates: list[float] = field(default_factory=lambda: [0.0] * LEG_COUNT)
    motor_spikes: int = 0
    sensory_spikes: int = 0
    finite: bool = True


def evaluate_locomotor(
    data: BrainData,
    forward_hz: float = 40.0,
    left_hz: float = 0.0,
    right_hz: float = 0.0,
    backward_hz: float = 0.0,
    onset: float = 0.0,
    duration: float = 8.0,
    hz: float = 120.0,
    configure: Callable[[LocomotorSim], None] | None = None,
) -> LocomotorTrial:
    """Drive the cord directly and integrate the body it moves.

    `hz` is the display rate the loop is driven at; the loop itself always
    advances on the fixed simulation tick, which is what check 9 exists to prove.
    """
    assert data.locomotor is not None
    cord = LocomotorSim(data.locomotor)
    body = SixLegDynamics([leg.geometry for leg in build_fly_model().legs])
    if configure is not None:
        configure(cord)

    trial = LocomotorTrial()
    accumulator = milliseconds = elapsed = 0.0
    x = y = heading = 0.0
    late_started = False
    low = [math.inf] * LEG_COUNT
    high = [-math.inf] * LEG_COUNT
    previous = [f.contact for f in body.feedback]

    for _ in range(int(duration * hz)):
        accumulator += 1.0 / hz
        while accumulator + 1e-10 >= TICK:
            accumulator -= TICK
            elapsed += TICK
            cord.set_descending("DNp09", "left", forward_hz)
            cord.set_descending("DNp09", "right", forward_hz)
            if elapsed >= onset:
                for steering in ("DNa01", "DNa02"):
                    cord.set_descending(steering, "left", left_hz)
                    cord.set_descending(steering, "right", right_hz)
                cord.set_descending("MDN", "left", backward_hz)
                cord.set_descending("MDN", "right", backward_hz)

            cord.feedback = body.feedback
            milliseconds += TICK * 1000
            steps = int(milliseconds + 1e-6)
            milliseconds -= steps
            cord.step(steps)
            motion = body.advance(cord.commands, TICK)

            trial.forward += motion.forward
            trial.lateral += motion.lateral
            trial.yaw += motion.yaw
            moved = math.hypot(motion.forward, motion.lateral)
            x += motion.lateral * math.cos(heading) - motion.forward * math.sin(heading)
            y += motion.lateral * math.sin(heading) + motion.forward * math.cos(heading)
            heading += motion.yaw

            settled = elapsed >= SETTLED_S
            if settled and not late_started:
                late_started = True
                trial.late_forward = trial.forward
                trial.late_yaw = trial.yaw
            elif settled:
                trial.late_distance += moved

            state = body.feedback
            for leg in range(LEG_COUNT):
                if state[leg].contact and not previous[leg]:
                    trial.contacts[leg] += 1
                low[leg] = min(low[leg], state[leg].hip_angle)
                high[leg] = max(high[leg], state[leg].hip_angle)
                trial.finite = trial.finite and math.isfinite(state[leg].foot_height)
                trial.finite = trial.finite and state[leg].foot_height > -0.001
            previous = [f.contact for f in state]

    trial.late_forward = trial.forward - trial.late_forward
    trial.late_yaw = trial.yaw - trial.late_yaw
    trial.ranges = [high[leg] - low[leg] if high[leg] > -math.inf else 0.0 for leg in range(6)]
    trial.motor_rates = [cord.mean_rate("motor", leg) for leg in range(LEG_COUNT)]
    trial.motor_spikes = cord.motor_spikes
    trial.sensory_spikes = cord.sensory_spikes
    return trial


def _joint_rotations(fly: Fly) -> list[Mat3]:
    return [
        node._rotation_matrix()
        for leg in fly.model.legs
        for node in (leg.root, leg.knee, leg.ankle)
    ]


def _toes(fly: Fly) -> list[np.ndarray]:
    toes = []
    for leg in fly.model.legs:
        chain = leg.root.local_matrix() @ leg.knee.local_matrix() @ leg.ankle.local_matrix()
        toes.append(chain @ np.array([leg.geometry.tarsus, 0.0, 0.0, 1.0], dtype=np.float32))
    return toes


def _rotation_angle(before: Mat3, after: Mat3) -> float:
    cosine = (float(np.trace(before.T @ after)) - 1.0) / 2.0
    return math.acos(min(1.0, max(-1.0, cosine)))


class _Report:
    def __init__(self) -> None:
        self.failures = 0

    def record(self, passed: bool, name: str, detail: str) -> None:
        if not passed:
            self.failures += 1
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")


def _drive_chain(
    data: BrainData, seed: int, frames: int
) -> tuple[Fly, float, list[int]]:
    """The whole live chain: FlyWire brain, nerve cord, mechanics, body.

    Returns the fly, the path it walked once settled, and its contact onsets.
    """
    sim = LIFSim(data.circuit, seed=seed, locomotor_circuit=data.locomotor)
    builder = SignalBuilder()
    fly = Fly((0.0, 0.0))
    fly.state = State.WALKING
    sim.stimulate(sim.groups.forward, 0.15, 10_000)

    settle = int(SETTLED_S / TICK)
    path = 0.0
    last = (fly.x, fly.y)
    contacts = [0] * LEG_COUNT
    previous = [f.contact for f in fly.leg_feedback]
    for frame in range(frames):
        sim.leg_feedback = fly.leg_feedback
        sim.step(9 if frame % 3 == 2 else 8)
        signals = builder.make(sim, TICK)
        # Isolate walking: every other command would end the window early for
        # reasons that have nothing to do with the motor path being tested.
        signals.escape = False
        signals.groom_drive = 0.0
        signals.nervous = 0.0
        signals.arousal = 0.0
        fly.update(TICK, BOUNDS, None, signals)
        if frame >= settle:
            path += math.dist((fly.x, fly.y), last)
            state = fly.leg_feedback
            for leg in range(LEG_COUNT):
                if state[leg].contact and not previous[leg]:
                    contacts[leg] += 1
            previous = [f.contact for f in state]
        last = (fly.x, fly.y)
    return fly, path, contacts


@dataclass
class _Phase:
    """One stretch of a transition fixture: how long, and what the brain says."""

    ticks: int
    walk: float = 0.0
    groom: float = 0.0
    sleep: bool = False


def transition_check(
    data: BrainData,
    seed: int,
    phases: list[_Phase],
    expected: list[str],
    setup: Callable[[Fly], None] | None = None,
    first_signals: Callable[[BrainSignals], None] | None = None,
    at_tick: Callable[[int, Fly], None] | None = None,
) -> tuple[bool, str]:
    """Walk the fly for real, then change its behaviour and watch for a jump.

    The warm-up is real motor walking rather than a reset pose, because the
    thing being tested is the handover out of one and into another.
    """
    sim = LIFSim(data.circuit, seed=seed, locomotor_circuit=data.locomotor)
    builder = SignalBuilder()
    fly = Fly((0.0, 0.0))
    fly.state = State.WALKING
    sim.stimulate(sim.groups.forward, 0.15, 20_000)

    def signals(frame: int, walk: float, groom: float, sleep: bool) -> BrainSignals:
        sim.leg_feedback = fly.leg_feedback
        sim.step(9 if frame % 3 == 2 else 8)
        value = builder.make(sim, TICK)
        value.escape = False
        value.nervous = 0.0
        value.arousal = 0.0
        value.walk_drive = walk
        value.groom_drive = groom
        value.sleep = sleep
        return value

    for frame in range(WARMUP_TICKS):
        fly.update(TICK, BOUNDS, None, signals(frame, 1.0, 0.0, False))
    if setup is not None:
        setup(fly)

    seen = [fly.state.value]
    worst_joint = worst_toe = worst_heading = worst_pitch = 0.0
    frame = WARMUP_TICKS
    first = True
    for phase in phases:
        for _ in range(phase.ticks):
            before_joints = _joint_rotations(fly)
            before_toes = _toes(fly)
            before_heading, before_pitch = fly.heading, fly.pitch

            value = signals(frame, phase.walk, phase.groom, phase.sleep)
            if first and first_signals is not None:
                first_signals(value)
            first = False
            mouse = (fly.x + 180.0 * math.cos(fly.heading), fly.y + 180.0 * math.sin(fly.heading))
            fly.update(TICK, BOUNDS, mouse if value.escape or value.nervous else None, value)
            if at_tick is not None:
                at_tick(frame - WARMUP_TICKS, fly)

            for was, now in zip(before_joints, _joint_rotations(fly), strict=True):
                worst_joint = max(worst_joint, _rotation_angle(was, now))
            for was, now in zip(before_toes, _toes(fly), strict=True):
                worst_toe = max(worst_toe, float(np.linalg.norm(now - was)))
            worst_heading = max(worst_heading, abs(angle_difference(before_heading, fly.heading)))
            worst_pitch = max(worst_pitch, abs(fly.pitch - before_pitch))
            if fly.state.value != seen[-1]:
                seen.append(fly.state.value)
            frame += 1

    # `expected` is a subsequence: the fixture cares that those states happened
    # in that order, not that nothing else did.
    remaining = list(expected)
    for state in seen:
        if remaining and state == remaining[0]:
            remaining.pop(0)
    smooth = (
        worst_joint < JOINT_JUMP_LIMIT
        and worst_toe < TOE_JUMP_LIMIT
        and worst_heading < HEADING_JUMP_LIMIT
        and worst_pitch < PITCH_JUMP_LIMIT
    )
    return not remaining and smooth, (
        f"states {' -> '.join(seen)}; per tick max joint {worst_joint:.3f} rad, "
        f"toe {worst_toe:.3f} units, heading {worst_heading:.3f} rad, "
        f"pitch {worst_pitch:.3f} rad"
    )


def run(seed: int | None = None) -> int:
    seed = DEFAULT_SEED if seed is None else seed
    data = load_brain_data()
    if data is None:
        print("no data/ — run etl.py first", file=sys.stderr)
        return 1
    if data.locomotor is None:
        print("no data/locomotor_circuit.json — run etl_malecns.py first", file=sys.stderr)
        return 1
    report = _Report()

    def check(name: str, run_check: Callable[[], tuple[bool, str]]) -> None:
        # Reseeded per check, from the check's own name, so one check cannot
        # move the ones after it.
        random.seed(f"{seed}:{name}")
        passed, detail = run_check()
        report.record(passed, name, detail)

    walking = evaluate_locomotor(data)

    def quiet() -> tuple[bool, str]:
        def silence_senses(cord: LocomotorSim) -> None:
            cord.feedback_enabled = False

        trial = evaluate_locomotor(
            data, forward_hz=0.0, duration=4.0, configure=silence_senses
        )
        return (
            trial.motor_spikes == 0 and trial.late_distance < 0.001,
            f"motor spikes {trial.motor_spikes}, travel {trial.late_distance:.5f}",
        )

    def recruits_all_legs() -> tuple[bool, str]:
        rates = walking.motor_rates
        return (
            all(rate > 0.5 for rate in rates),
            "motor rates " + " ".join(f"{rate:.1f}" for rate in rates) + " Hz",
        )

    def sustains_walking() -> tuple[bool, str]:
        ok = (
            walking.forward > 10
            and walking.late_distance > 10
            and all(count >= 2 for count in walking.contacts)
            and walking.finite
        )
        return ok, (
            f"forward {walking.forward:.2f}, settled path {walking.late_distance:.2f}, "
            f"contacts {walking.contacts}"
        )

    def synapses_carry_it() -> tuple[bool, str]:
        def cut(cord: LocomotorSim) -> None:
            cord.synapses_enabled = False

        trial = evaluate_locomotor(data, duration=4.0, configure=cut)
        return (
            trial.motor_spikes == 0 and trial.late_distance < 0.001,
            f"motor spikes {trial.motor_spikes}, travel {trial.late_distance:.5f}",
        )

    def motor_lesion() -> tuple[bool, str]:
        def lesion(cord: LocomotorSim) -> None:
            cord.silenced = cord.indices("motor")

        trial = evaluate_locomotor(data, duration=4.0, configure=lesion)
        return (
            trial.motor_spikes == 0 and trial.late_distance < 0.001,
            f"motor spikes {trial.motor_spikes}, travel {trial.late_distance:.5f}",
        )

    def steering_turns_the_body() -> tuple[bool, str]:
        common = {"forward_hz": 30.0, "onset": SETTLED_S, "duration": 10.0}
        straight = evaluate_locomotor(data, **common)
        left = evaluate_locomotor(data, left_hz=70.0, **common)
        right = evaluate_locomotor(data, right_hz=70.0, **common)
        return (
            left.late_yaw - straight.late_yaw > 0.10
            and right.late_yaw - straight.late_yaw < -0.10,
            f"settled yaw {straight.late_yaw:+.3f} straight, {left.late_yaw:+.3f} left, "
            f"{right.late_yaw:+.3f} right",
        )

    def moonwalk_is_physical() -> tuple[bool, str]:
        trial = evaluate_locomotor(data, forward_hz=0.0, backward_hz=70.0, duration=10.0)
        return trial.late_forward < -5, f"settled forward {trial.late_forward:.2f}"

    def feedback_reaches_the_circuit() -> tuple[bool, str]:
        def open_loop(cord: LocomotorSim) -> None:
            cord.feedback_enabled = False

        blind = evaluate_locomotor(data, configure=open_loop)
        return (
            walking.sensory_spikes > 0 and walking.motor_spikes != blind.motor_spikes,
            f"motor spikes {walking.motor_spikes} closed vs {blind.motor_spikes} open, "
            f"sensory {walking.sensory_spikes}",
        )

    def display_rate_does_not_matter() -> tuple[bool, str]:
        slow = evaluate_locomotor(data, hz=60.0)
        return (
            abs(slow.forward - walking.forward) < 1e-6
            and slow.contacts == walking.contacts
            and slow.motor_spikes == walking.motor_spikes,
            f"forward {slow.forward:.6f} @60Hz vs {walking.forward:.6f} @120Hz, "
            f"motor spikes {slow.motor_spikes} vs {walking.motor_spikes}",
        )

    def live_chain_walks() -> tuple[bool, str]:
        fly, path, contacts = _drive_chain(data, seed, frames=1200)
        return (
            path > 20 and all(count >= 4 for count in contacts),
            f"settled path {path:.2f}, contacts {contacts}, state {fly.state.value}",
        )

    def silence_cannot_be_bypassed() -> tuple[bool, str]:
        fly = Fly((0.0, 0.0))
        fly.state = State.WALKING
        fly.speed = 70.0
        fly.heading = 0.0
        signals = BrainSignals(
            walk_drive=1.0,
            turn_bias=1.0,
            leg_commands=[LegMotorCommand() for _ in range(LEG_COUNT)],
        )
        for _ in range(120):
            fly.update(TICK, BOUNDS, None, signals)
        moved = math.hypot(fly.x, fly.y)
        return (
            fly.state is State.WALKING and moved < 1e-7 and abs(fly.heading) < 1e-7,
            f"moved {moved:.2e}, heading {fly.heading:+.2e} rad",
        )

    def rendered_toes_match_physics() -> tuple[bool, str]:
        fly, _, _ = _drive_chain(data, seed, frames=480)
        physical = fly.leg_dynamics.feedback
        worst = 0.0
        for rendered, actual in zip(fly.leg_feedback, physical, strict=True):
            worst = max(
                worst,
                abs(rendered.foot_x - actual.foot_x),
                abs(rendered.foot_y - actual.foot_y),
                abs(rendered.foot_height - actual.foot_height),
            )
        return worst < 2e-5, f"max toe disagreement {worst:.2e} units"

    def tempo_reaches_the_mechanics() -> tuple[bool, str]:
        commands = [LegMotorCommand(retract=0.3, depress=0.2, flex=0.2) for _ in range(LEG_COUNT)]
        worst = 0.0
        for tempo in (0.5, 1.0, 2.0):
            fly = Fly((0.0, 0.0))
            fly.state = State.WALKING
            signals = BrainSignals(walk_drive=1.0, tempo=tempo, leg_commands=commands)
            for _ in range(2):  # let the mechanics take control first
                fly.update(TICK, BOUNDS, None, signals)
            reference = copy.deepcopy(fly.leg_dynamics)
            fly.update(TICK, BOUNDS, None, signals)
            reference.advance(commands, TICK * tempo)
            for produced, expected in zip(
                fly.leg_dynamics.feedback, reference.feedback, strict=True
            ):
                worst = max(worst, abs(produced.knee_angle - expected.knee_angle))
        return worst < 1e-9, f"max knee disagreement {worst:.2e} rad across tempo 0.5/1/2"

    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("MaleCNS quiet without descending or sensory drive", quiet),
        ("MaleCNS recruits motor neurons in all six legs", recruits_all_legs),
        ("MaleCNS sustains walking after settling", sustains_walking),
        ("cutting synapses abolishes descending-to-motor response", synapses_carry_it),
        ("motor lesion abolishes body propulsion", motor_lesion),
        ("bilateral steering perturbations change motor-driven yaw", steering_turns_the_body),
        ("MDN produces physical backward stepping", moonwalk_is_physical),
        ("physical feedback changes native circuit activity", feedback_reaches_the_circuit),
        ("coupled model is independent of 60/120 Hz display", display_rate_does_not_matter),
        ("complete live brain-to-body chain sustains stepping", live_chain_walks),
        ("legacy speed and turning cannot bypass motor silence", silence_cannot_be_bypassed),
        ("rendered toes agree with physical feedback", rendered_toes_match_physics),
        ("thermal tempo reaches active motor mechanics", tempo_reaches_the_mechanics),
    ]

    def groom_and_resume() -> tuple[bool, str]:
        return transition_check(
            data,
            seed,
            [_Phase(120, groom=1.0), _Phase(240, walk=1.0)],
            ["walking", "grooming", "idle", "walking"],
        )

    def sleep_and_wake() -> tuple[bool, str]:
        return transition_check(
            data,
            seed,
            [_Phase(120), _Phase(120, sleep=True), _Phase(240, walk=1.0)],
            ["walking", "idle", "sleeping", "grooming", "idle", "walking"],
        )

    def flight_and_landing() -> tuple[bool, str]:
        def escape(value: BrainSignals) -> None:
            value.escape = True

        return transition_check(
            data,
            seed,
            [_Phase(480), _Phase(180, walk=1.0)],
            ["walking", "flying", "idle", "walking"],
            first_signals=escape,
        )

    def nervous_turn() -> tuple[bool, str]:
        def startle(value: BrainSignals) -> None:
            value.nervous = 0.9

        return transition_check(
            data,
            seed,
            [_Phase(120, walk=1.0)],
            ["walking"],
            first_signals=startle,
        )

    def ledge_endpoint() -> tuple[bool, str]:
        """Reverse at the end of an edge, and drop when that edge is dragged away."""
        state = {"reversed": False, "took_off": False, "shift": 0.0, "before": (0.0, 0.0)}

        def place(fly: Fly) -> None:
            fly.terrain = [Ledge(y=0.0, x0=-40.0, x1=40.0, key=42)]
            fly.ledge = fly.terrain[0]
            fly.x, fly.y, fly.heading = 39.0, 0.0, 0.0

        def watch(tick: int, fly: Fly) -> None:
            if tick == 60:
                state["reversed"] = fly.state is State.WALKING and (
                    abs(angle_difference(0.0, fly.heading)) > 0.5
                )
                state["before"] = (fly.x, fly.y)
                # Same edge, same height, dragged 400 units to the right.
                fly.terrain = [Ledge(y=0.0, x0=360.0, x1=440.0, key=42)]
            elif tick > 60 and not state["took_off"] and fly.state is State.FLYING:
                state["took_off"] = True
                state["shift"] = math.dist((fly.x, fly.y), state["before"])

        ok, detail = transition_check(
            data,
            seed,
            [_Phase(120, walk=1.0)],
            ["walking", "flying"],
            setup=place,
            at_tick=watch,
        )
        moved_support = state["took_off"] and state["shift"] < 1.0
        return ok and bool(state["reversed"]) and moved_support, (
            f"{detail}; reversed at the end={state['reversed']}, "
            f"took off when dragged={state['took_off']} "
            f"after moving {state['shift']:.3f} units"
        )

    checks += [
        ("transition: groom and resume", groom_and_resume),
        ("transition: idle, sleep and wake", sleep_and_wake),
        ("transition: flight and landing", flight_and_landing),
        ("transition: nervous turn", nervous_turn),
        ("transition: ledge endpoint and dragged support", ledge_endpoint),
    ]
    for name, run_check in checks:
        check(name, run_check)

    print(
        "ALL LOCOMOTOR TESTS PASS"
        if report.failures == 0
        else f"{report.failures} LOCOMOTOR FAILURES"
    )
    return 0 if report.failures == 0 else 1
