"""Turning population firing rates into body commands.

Port of the SignalBuilder class in main.swift. It is shared by the running app
and by the behaviour test suite, so both exercise the identical mapping.
"""

from __future__ import annotations

from . import constants as k
from .behavior import lag
from .sim import BrainSignals, LIFSim


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


class SignalBuilder:
    def __init__(self) -> None:
        self._dna_baseline = 0.0

    def make(self, sim: LIFSim, dt: float) -> BrainSignals:
        difference = sim.rate_dna_left - sim.rate_dna_right
        # The connectome carries a standing left/right asymmetry in the steering
        # neurons. Adapting it out over ~8 s keeps steady walking straight, so
        # only transient asymmetries - a threat on one side, a click - steer.
        self._dna_baseline += (difference - self._dna_baseline) * lag(
            1.0 / k.DNA_ADAPT_TAU_S, dt
        )

        return BrainSignals(
            escape=sim.consume_gf(),
            nervous=clamp(sim.rate_loom * k.NERVOUS_PER_HZ, 0.0, 1.0),
            turn_bias=clamp(
                (difference - self._dna_baseline) * k.TURN_BIAS_PER_HZ,
                -k.TURN_BIAS_LIMIT,
                k.TURN_BIAS_LIMIT,
            ),
            backward=sim.rate_mdn > k.BACKWARD_HZ,
            walk_drive=clamp(sim.rate_forward * k.WALK_DRIVE_PER_HZ, 0.0, k.WALK_DRIVE_LIMIT),
            groom_drive=sim.rate_groom * k.GROOM_DRIVE_PER_HZ,
            wing_drive=clamp(sim.rate_escape_wing * k.WING_DRIVE_PER_HZ, 0.0, k.WING_DRIVE_LIMIT),
            arousal=clamp(sim.rate_population * k.AROUSAL_PER_HZ, 0.0, 1.0),
            leg_commands=sim.locomotor.commands if sim.locomotor is not None else None,
        )
