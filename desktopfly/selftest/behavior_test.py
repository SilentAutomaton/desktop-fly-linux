"""Seventeen end-to-end checks: stimulate real neurons, watch the body react.

Port of runBehaviorTest in main.swift. Seven scenarios drive the network and
assert what the fly does; ten body checks drive the body directly with
hand-built signals and assert the mechanics. Together with sim_test they are
the acceptance criteria for this port.
"""

from __future__ import annotations

import random
import sys
from collections.abc import Callable

import numpy.typing as npt

from desktopfly import constants as k
from desktopfly.behavior import Fly, State
from desktopfly.dataset import BrainData, load_brain_data
from desktopfly.environment import Ledge, circadian_activity
from desktopfly.selftest import DEFAULT_SEED
from desktopfly.signals import SignalBuilder
from desktopfly.sim import BrainSignals, LIFSim

BOUNDS = (1512.0, 982.0)  # upstream's reference display
DT = 1.0 / 60.0


class _Report:
    def __init__(self) -> None:
        self.failures = 0

    def record(self, passed: bool, name: str, detail: str) -> None:
        if not passed:
            self.failures += 1
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")


def _scenario(
    report: _Report,
    data: BrainData,
    name: str,
    stimulate: Callable[[LIFSim], None],
    hold: float,
    check: Callable[[Fly], bool],
    describe: Callable[[Fly], str],
    setup: Callable[[Fly], None] | None = None,
    seed: int | None = None,
) -> None:
    sim = LIFSim(data.circuit, seed=seed)
    builder = SignalBuilder()
    fly = Fly((0.0, 0.0))
    fly.state = State.IDLE
    fly.speed = 0.0
    if setup is not None:
        setup(fly)

    # Settle the network and drain any start-up latch before stimulating.
    sim.step(400)
    sim.consume_gf()
    stimulate(sim)

    passed = False
    for _ in range(int(hold / DT)):
        sim.step(round(DT * 1000))
        signals = builder.make(sim, DT)
        fly.update(DT, BOUNDS, None, signals)
        if check(fly):
            passed = True
            break
    report.record(passed, name, describe(fly))


def _walk_signals() -> BrainSignals:
    return BrainSignals(walk_drive=0.6)


def run(seed: int | None = None) -> int:
    # Seeded by default, for the reason given in sim_test: the body's wandering
    # is as random as the network's noise, and both feed these thresholds.
    seed = DEFAULT_SEED if seed is None else seed
    random.seed(seed)
    data = load_brain_data()
    if data is None:
        print("no data/ — run etl.py first", file=sys.stderr)
        return 1
    report = _Report()

    def group(name: str) -> Callable[[LIFSim], npt.NDArray]:
        return lambda sim: getattr(sim.groups, name)

    _scenario(
        report,
        data,
        "GF stim -> escape flight",
        stimulate=lambda sim: sim.stimulate(sim.groups.gf, 0.5, 40),
        hold=0.5,
        check=lambda fly: fly.state is State.FLYING,
        describe=lambda fly: f"state={fly.state.value}",
        seed=seed,
    )
    _scenario(
        report,
        data,
        "DNg11 stim -> grooming",
        stimulate=lambda sim: sim.stimulate(sim.groups.groom, 0.25, 600),
        hold=1.5,
        check=lambda fly: fly.state is State.GROOMING,
        describe=lambda fly: f"state={fly.state.value}",
        seed=seed,
    )
    _scenario(
        report,
        data,
        "DNp09 stim -> walks, speed rises (capped)",
        stimulate=lambda sim: sim.stimulate(sim.groups.forward, 0.25, 1200),
        hold=1.5,
        check=lambda fly: fly.state is State.WALKING and 40 < fly.speed < 100,
        describe=lambda fly: f"state={fly.state.value} speed={int(fly.speed)}",
        seed=seed,
    )
    _scenario(
        report,
        data,
        "MDN stim (from idle) -> backward walk",
        stimulate=lambda sim: sim.stimulate(sim.groups.mdn, 0.3, 600),
        hold=1.2,
        check=lambda fly: fly.backward_timer > 0,
        describe=lambda fly: f"backward_timer={fly.backward_timer:.2f}",
        seed=seed,
    )

    def steer_setup(fly: Fly) -> None:
        fly.state = State.WALKING
        fly.speed = 30.0
        fly.heading = 0.0

    _scenario(
        report,
        data,
        "DNa-left stim -> left (CCW) turn while walking",
        stimulate=lambda sim: sim.stimulate(sim.groups.dna_left, 0.3, 900),
        hold=1.4,
        setup=steer_setup,
        check=lambda fly: fly.heading > 0.25,
        describe=lambda fly: f"heading change {fly.heading:+.2f} rad",
        seed=seed,
    )

    def moderate_loom(sim: LIFSim) -> None:
        sim.loom_left = 0.45
        sim.loom_right = 0.45

    _scenario(
        report,
        data,
        "moderate loom -> fear response (dart or escape)",
        stimulate=moderate_loom,
        hold=1.0,
        check=lambda fly: (
            (fly.state is State.WALKING and fly.speed > 100) or fly.state is State.FLYING
        ),
        describe=lambda fly: f"state={fly.state.value} speed={int(fly.speed)}",
        seed=seed,
    )
    _scenario(
        report,
        data,
        "tap near fly -> startle escape via sensory pathway",
        stimulate=lambda sim: sim.stimulate(sim.groups.sensory, 0.45, 150),
        hold=0.8,
        check=lambda fly: fly.state is State.FLYING,
        describe=lambda fly: f"state={fly.state.value}",
        seed=seed,
    )

    # -- body-level checks: hand-built signals, no network --------------------

    def ledge_follow() -> tuple[bool, str]:
        fly = Fly((0.0, -55.0))
        fly.state = State.WALKING
        fly.speed = 30.0
        fly.heading = 0.0
        fly.terrain = [Ledge(y=-40.0, x0=-300.0, x1=300.0, key=1)]
        for _ in range(240):
            fly.update(DT, BOUNDS, None, _walk_signals())
            if fly.ledge is not None and abs(fly.y + 40) < 8:
                return True, f"attached, y={int(fly.y)}"
        return False, f"state={fly.state.value} y={int(fly.y)} ledge={fly.ledge is not None}"

    def ground_vanishes() -> tuple[bool, str]:
        fly = Fly((0.0, -40.0))
        fly.state = State.WALKING
        fly.speed = 25.0
        fly.heading = 0.0
        fly.terrain = [Ledge(y=-40.0, x0=-300.0, x1=300.0, key=1)]
        fly.ledge = fly.terrain[0]
        fly.terrain = []  # the window closed under its feet
        for _ in range(60):
            fly.update(DT, BOUNDS, None, _walk_signals())
            if fly.state is State.FLYING:
                return True, "took off"
        return False, f"state={fly.state.value}"

    def sleep_and_wake() -> tuple[bool, str]:
        fly = Fly((0.0, 0.0))
        fly.state = State.IDLE
        signals = BrainSignals(sleep=True)
        for _ in range(60):
            fly.update(DT, BOUNDS, None, signals)
        if fly.state is not State.SLEEPING:
            return False, f"no sleep: {fly.state.value}"
        signals.sleep = False
        fly.update(DT, BOUNDS, None, signals)
        return fly.state is State.GROOMING, f"woke to {fly.state.value}"

    def thermal_tempo_scales_speed() -> tuple[bool, str]:
        fly = Fly((0.0, 0.0))
        fly.state = State.WALKING
        fly.speed = 20.0
        fly.heading = 0.0
        cool = _walk_signals()
        cool.tempo = 1.0
        for _ in range(120):
            fly.update(DT, BOUNDS, None, cool)
        cool_speed = fly.speed
        hot = _walk_signals()
        hot.tempo = 1.5
        for _ in range(120):
            fly.update(DT, BOUNDS, None, hot)
        hot_speed = fly.speed
        return (
            fly.state is State.WALKING and hot_speed > cool_speed + 10,
            f"cool {int(cool_speed)} -> hot {int(hot_speed)} pt/s",
        )

    def altitude_drives_scale() -> tuple[bool, str]:
        def flight(escape: bool, effort: float | None) -> tuple[float, float]:
            fly = Fly((0.0, 0.0))
            fly.state = State.IDLE
            fly.start_flight(BOUNDS, escape=escape, effort=effort)
            max_altitude = max_scale = 0.0
            frames = 0
            while fly.state is State.FLYING and frames < 400:
                frames += 1
                fly.update(DT, BOUNDS, None, BrainSignals())
                max_altitude = max(max_altitude, fly.altitude)
                max_scale = max(max_scale, fly.node.scale[0])
            return max_altitude, max_scale

        escape_alt, escape_scale = flight(escape=True, effort=None)
        casual_alt, casual_scale = flight(escape=False, effort=0.45)
        ok = (
            escape_alt > casual_alt + 0.15
            and escape_scale > k.FLY_SCALE * 1.5
            and abs(escape_scale - k.FLY_SCALE * (1 + 0.8 * escape_alt)) < 0.15
        )
        return ok, (
            f"escape alt {escape_alt:.2f} scale {escape_scale:.2f} | "
            f"casual alt {casual_alt:.2f} scale {casual_scale:.2f}"
        )

    def wings_beat() -> tuple[bool, str]:
        fly = Fly((0.0, 0.0))
        fly.state = State.IDLE
        fly.start_flight(BOUNDS, effort=0.8)
        low, high = float("inf"), -float("inf")
        for _ in range(30):
            if fly.state is not State.FLYING:
                break
            fly.update(DT, BOUNDS, None, BrainSignals())
            roll = fly.model.folded_wings.children[0].euler[2]
            low, high = min(low, roll), max(high, roll)
        return high - low > 0.25, f"wing sweep {high - low:.2f} rad over 0.5 s"

    def escape_dn_raises_effort() -> tuple[bool, str]:
        fly = Fly((0.0, 0.0))
        fly.state = State.IDLE
        fly.start_flight(BOUNDS, effort=0.5)
        for _ in range(12):
            fly.update(DT, BOUNDS, None, BrainSignals())
        calm_effort = fly.effort_current
        hot = BrainSignals(wing_drive=1.0, arousal=0.6)
        for _ in range(12):
            if fly.state is not State.FLYING:
                break
            fly.update(DT, BOUNDS, None, hot)
        return (
            fly.state is State.FLYING and fly.effort_current > calm_effort + 0.2,
            f"effort {calm_effort:.2f} -> {fly.effort_current:.2f}",
        )

    def threat_raises_wings() -> tuple[bool, str]:
        fly = Fly((0.0, 0.0))
        fly.state = State.WALKING
        fly.speed = 20.0
        fly.dart_cooldown = 99.0  # isolate the posture from the darting reflex
        threat = BrainSignals(wing_drive=0.9, walk_drive=0.4)
        for _ in range(40):
            fly.update(DT, BOUNDS, None, threat)
        pitch = fly.model.folded_wings.children[0].euler[0]
        return (
            fly.state is not State.FLYING and fly.wing_raise > 0.6 and pitch < -0.2,
            f"raise {fly.wing_raise:.2f}, wing tilt {pitch:.2f} rad",
        )

    def landing_is_smooth() -> tuple[bool, str]:
        fly = Fly((0.0, 0.0))
        fly.state = State.IDLE
        fly.start_flight(BOUNDS, escape=True)
        previous_scale = fly.node.scale[0]
        previous_z = fly.node.position[2]
        max_scale_step = max_z_step = 0.0
        after_landing, frames, landed = 20, 0, False
        while after_landing > 0 and frames < 600:
            frames += 1
            fly.update(DT, BOUNDS, None, BrainSignals())
            max_scale_step = max(max_scale_step, abs(fly.node.scale[0] - previous_scale))
            max_z_step = max(max_z_step, abs(fly.node.position[2] - previous_z))
            previous_scale = fly.node.scale[0]
            previous_z = fly.node.position[2]
            if fly.state is not State.FLYING:
                landed = True
                after_landing -= 1
        return (
            landed and max_scale_step < 0.2 and max_z_step < 25,
            f"landed={'yes' if landed else 'NO'}, max per-frame dscale "
            f"{max_scale_step:.2f}, dz {max_z_step:.1f}",
        )

    def circadian_shape() -> tuple[bool, str]:
        night, dawn = circadian_activity(3), circadian_activity(9)
        siesta, dusk = circadian_activity(14), circadian_activity(18)
        ok = night < 0.4 and dawn > 0.9 and 0.3 < siesta < 0.7 and dusk > 0.9
        return ok, f"3h {night:.2f}, 9h {dawn:.2f}, 14h {siesta:.2f}, 18h {dusk:.2f}"

    body_checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("ledge attach + follow window edge", ledge_follow),
        ("window closes underfoot -> takeoff", ground_vanishes),
        ("sleep signal -> sleeping; wake -> grooming", sleep_and_wake),
        ("thermal tempo scales walking speed", thermal_tempo_scales_speed),
        ("flight: altitude drives scale; escape flies higher than casual", altitude_drives_scale),
        ("flight: wings actually beat", wings_beat),
        ("escape-DN activity mid-flight raises wing-beat effort", escape_dn_raises_effort),
        ("threat while grounded raises the wings (no takeoff)", threat_raises_wings),
        ("landing is smooth: no scale/height snap at touchdown", landing_is_smooth),
        ("circadian curve: siesta + night dips, dawn/dusk peaks", circadian_shape),
    ]
    for name, check in body_checks:
        ok, detail = check()
        report.record(ok, name, detail)

    print("ALL BEHAVIOR TESTS PASS" if report.failures == 0 else f"{report.failures} FAILURES")
    return 0 if report.failures == 0 else 1
