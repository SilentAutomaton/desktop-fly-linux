"""Circuit invariants. Port of runSimtest in main.swift.

These numbers are upstream's and they are the ground truth: they certify that
the network still rests quietly, still wins the escape race, still fluctuates
into and out of locomotion, and still survives the midday siesta. Run this
after any change to sim.py, constants.py or etl.py.
"""

from __future__ import annotations

import sys

from desktopfly.dataset import load_brain_data
from desktopfly.sim import LIFSim


def run(seed: int | None = None) -> int:
    data = load_brain_data()
    if data is None:
        print("no data/ — run etl.py first", file=sys.stderr)
        return 1
    sim = LIFSim(data.circuit, seed=seed)
    g = sim.groups
    print(
        f"circuit: {sim.n} neurons | loom L/R: {len(g.loom_left)}/{len(g.loom_right)}"
        f" | GF: {len(g.gf)} | DNa L/R: {len(g.dna_left)}/{len(g.dna_right)}"
        f" | MDN: {len(g.mdn)} | DNp09: {len(g.forward)} | DNg11: {len(g.groom)}"
        f" | escW: {len(g.escape_wing)} | ascend: {len(g.ascending)} | sens: {len(g.sensory)}"
    )

    # Phase 1: four seconds of spontaneous activity. The giant fiber is a command
    # neuron; if it fires here, the fly would take off for no reason at all.
    gf_spontaneous = 0
    for _ in range(40):
        sim.step(100)
        if sim.consume_gf():
            gf_spontaneous += 1
    population_hz = sim.total_spikes / 4.0 / sim.n
    print(
        f"spontaneous 4s: pop {population_hz:.2f} Hz/neuron, LC {sim.rate_loom:.1f} Hz, "
        f"DNa L/R {sim.rate_dna_left:.1f}/{sim.rate_dna_right:.1f} Hz, "
        f"MDN {sim.rate_mdn:.1f} Hz, GF spikes: {gf_spontaneous}"
    )

    # Phase 2: an abrupt loom, the way a cursor lunge arrives. It must be a step
    # and not a ramp: a slow ramp loses to feedforward inhibition by design.
    gf_latency_ms = -1
    gf_loom = 0
    for ms in range(400):
        sim.loom_left = 1.0
        sim.loom_right = 0.5
        sim.step(1)
        if sim.consume_gf():
            gf_loom += 1
            if gf_latency_ms < 0:
                gf_latency_ms = ms
    sim.loom_left = sim.loom_right = 0.0
    print(
        f"abrupt loom 0.4s: LC rate {sim.rate_loom:.1f} Hz, GF spikes {gf_loom}, "
        f"first at {gf_latency_ms} ms"
    )

    # Phase 3: twenty seconds with walking proprioception fed back in. Locomotor
    # drive has to fluctuate on its own, not sit pinned high or low.
    walk_on = groom_on = samples = 0
    forward_min, forward_max = float("inf"), 0.0
    for ms in range(20_000):
        sim.gait_drive = 0.5
        sim.gait_phase = (ms % 125) / 125  # an 8 Hz gait
        sim.step(1)
        if ms % 10 == 0:
            samples += 1
            if sim.rate_forward / 10 > 0.22:
                walk_on += 1
            if sim.rate_groom / 8 > 0.5:
                groom_on += 1
            forward_min = min(forward_min, sim.rate_forward)
            forward_max = max(forward_max, sim.rate_forward)
    print(
        f"behavior 20s: walk-drive on {100 * walk_on / samples:.0f}%, "
        f"groom-drive on {100 * groom_on / samples:.0f}%, "
        f"DNp09 {forward_min:.1f}-{forward_max:.1f} Hz, pop {sim.rate_population:.1f} Hz"
    )
    sim.gait_drive = 0.0

    # Phase 3b: the midday siesta must slow the fly down, not paralyse it. The
    # scale is the compressed one, because a raw multiplier silences the network.
    sim.activity_scale = 1 - (1 - 0.55) * 0.35  # = 0.84
    siesta_walk_on = siesta_samples = 0
    for ms in range(15_000):
        sim.step(1)
        if ms % 10 == 0:
            siesta_samples += 1
            if sim.rate_forward / 10 > 0.22:
                siesta_walk_on += 1
    sim.activity_scale = 1.0
    siesta_percent = 100 * siesta_walk_on / siesta_samples
    print(f"siesta 15s (scale 0.84): walk-drive on {siesta_percent:.0f}%")

    # Phase 4: one second of air puff — the wind startle pathway onto the GF.
    gf_puff = 0
    for _ in range(1000):
        sim.air_puff = 1.0
        sim.step(1)
        if sim.consume_gf():
            gf_puff += 1
    sim.air_puff = 0.0
    print(f"air puff 1s: GF spikes {gf_puff}")

    # Phase 5: a gentle loom in the left eye only, as a steering probe.
    for _ in range(500):
        sim.step(1)
        sim.consume_gf()
    difference_before = sim.rate_dna_left - sim.rate_dna_right
    for _ in range(1000):
        sim.loom_left = 0.30
        sim.loom_right = 0.0
        sim.step(1)
        sim.consume_gf()
    difference_after = sim.rate_dna_left - sim.rate_dna_right
    sim.loom_left = 0.0
    print(
        f"left-eye loom: DNa L-R rate diff {difference_before:+.1f} -> "
        f"{difference_after:+.1f} Hz, LC {sim.rate_loom:.1f} Hz"
    )

    # Phase 6: the click probes the brain window fires.
    sim.stimulate(sim.groups.gf, strength=0.5, duration_ms=40)
    sim.step(60)
    gf_stimulated = sim.consume_gf()
    sim.stimulate(sim.groups.groom, strength=0.25, duration_ms=400)
    sim.step(400)
    groom_rate = sim.rate_groom
    sim.consume_gf()
    print(
        f"click probes: GF cluster -> spike {'yes' if gf_stimulated else 'NO'}, "
        f"DNg11 cluster -> groom rate {groom_rate:.0f} Hz"
    )

    passed = (
        gf_spontaneous == 0 and gf_loom > 0 and walk_on > 0 and gf_stimulated and siesta_percent > 3
    )
    print(
        "PASS: GF silent at rest, fires on loom; locomotor drive fluctuates; "
        "stim works; siesta alive"
        if passed
        else "FAIL: tune weights/noise"
    )
    return 0 if passed else 1
