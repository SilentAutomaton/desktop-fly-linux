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

from desktopfly import constants as k
from desktopfly.behavior import Fly, State
from desktopfly.dataset import LEG_COUNT, BrainData, load_brain_data
from desktopfly.geometry import build_fly_model
from desktopfly.legdynamics import LegMotorCommand, SixLegDynamics
from desktopfly.locomotor import LocomotorSim
from desktopfly.selftest import DEFAULT_SEED
from desktopfly.signals import SignalBuilder
from desktopfly.sim import BrainSignals, LIFSim

BOUNDS = (1512.0, 982.0)
TICK = k.SIMULATION_TICK_S
SETTLED_S = 3.0  # everything before this is the model getting going


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
    for name, run_check in checks:
        check(name, run_check)

    print(
        "ALL LOCOMOTOR TESTS PASS"
        if report.failures == 0
        else f"{report.failures} LOCOMOTOR FAILURES"
    )
    return 0 if report.failures == 0 else 1
