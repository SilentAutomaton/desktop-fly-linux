"""The leaky-integrate-and-fire network that decides what the fly does.

Port of Sim.swift. Every constant is upstream's; see constants.py for their
origin and for the ones that must not be retuned casually.

The one structural change from the original is vectorisation: upstream walks
the 668 neurons in a Swift loop each millisecond, this port does the same
millisecond as a handful of numpy array operations, and delivers spikes by
summing the weight-matrix rows of the neurons that fired. The arithmetic and the
ordering inside a millisecond are identical, so both upstream test suites apply
unchanged. State is float64 rather than Swift's Float; at 668 neurons the cost
is irrelevant and the accuracy is strictly better.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from . import constants as k
from .dataset import Circuit

# Roles that share one firing-rate readout. The index is the slot in the rate
# vector counted each millisecond; the giant fiber is not here because it is
# latched per spike rather than averaged.
RATE_LOOM = 0
RATE_DNA_L = 1
RATE_DNA_R = 2
RATE_MDN = 3
RATE_FORWARD = 4
RATE_GROOM = 5
RATE_ESCAPE_WING = 6
RATE_GROUPS = 7

# Milliseconds of spontaneous-noise draws taken per numpy call.
NOISE_BLOCK_MS = 64


@dataclass
class BrainSignals:
    """What the brain tells the body each frame. Port of Sim.swift BrainSignals."""

    escape: bool = False  # the giant fiber spiked: take off now
    nervous: float = 0.0  # looming-detector population rate, 0..1
    turn_bias: float = 0.0  # rad/s from the DNa01/DNa02 left-right difference
    backward: bool = False  # MDN burst: walk backwards
    walk_drive: float = 0.0  # DNp09 forward-walking command, ~0..1.3
    groom_drive: float = 0.0  # DNg11 grooming command, ~0..1.5
    wing_drive: float = 0.0  # DNp02/04/11 escape-manoeuvre command, ~0..1.3
    arousal: float = 0.0  # whole-population activity, ~0..1
    tempo: float = 1.0  # thermal scaling of locomotion
    sleep: bool = False  # circadian rhythm plus user idleness


@dataclass
class SpikeEvent:
    neuron: int
    is_gf: bool


class SpikeBus:
    """Bounded, locked hand-off of spikes to the brain window.

    Port of Sim.swift SpikeBus. The lock is kept even though both ends
    currently run on the GTK main loop, because this is the seam where a worker
    thread would be added.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[SpikeEvent] = []

    def push(self, events: list[SpikeEvent]) -> None:
        if not events:
            return
        with self._lock:
            self._events.extend(events)
            excess = len(self._events) - k.SPIKE_BUS_CAPACITY
            if excess > 0:
                del self._events[:excess]

    def pop_all(self) -> list[SpikeEvent]:
        with self._lock:
            events = self._events
            self._events = []
        return events


@dataclass
class _Stim:
    """An "optogenetic" stimulation requested by a brain-window click."""

    indices: npt.NDArray[np.int64]
    strength: float
    duration_ms: int
    until_ms: int = 0


@dataclass
class _Groups:
    """Neuron indices per role, filled once from the circuit file."""

    loom_left: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    loom_right: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    gf: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    dna_left: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    dna_right: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    mdn: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    forward: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    groom: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    escape_wing: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    ascending: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))
    sensory: npt.NDArray[np.int64] = field(default_factory=lambda: np.empty(0, np.int64))


class LIFSim:
    """668 real neurons, 18968 real signed synapses, integrated at 1 kHz."""

    def __init__(
        self, circuit: Circuit, spike_bus: SpikeBus | None = None, seed: int | None = None
    ):
        self.n = len(circuit.neurons)
        self.roles = [neuron.role for neuron in circuit.neurons]
        self.types = [neuron.type for neuron in circuit.neurons]
        self.positions = np.asarray([neuron.pos for neuron in circuit.neurons], dtype=np.float32)
        self.spike_bus = spike_bus
        self._rng = np.random.default_rng(seed)

        self._v = np.zeros(self.n, dtype=np.float64)
        self._refractory = np.zeros(self.n, dtype=np.float64)
        self._inhibitory_queue = np.zeros((k.INHIBITORY_QUEUE_SLOTS, self.n), dtype=np.float64)
        # A slot is only worth adding when something was scheduled into it; most
        # milliseconds schedule nothing and scanning the row would cost more.
        self._queue_loaded = [False] * k.INHIBITORY_QUEUE_SLOTS
        self._queue_head = 0

        # Reused per-millisecond buffers, so the hot loop allocates nothing.
        self._drive = np.zeros(self.n, dtype=np.float64)
        self._scratch = np.zeros(self.n, dtype=np.float64)
        self._noise_hit = np.zeros(self.n, dtype=bool)
        self._refractory_hit = np.zeros(self.n, dtype=bool)
        self._resting = np.zeros(self.n, dtype=bool)
        self._spike_hit = np.zeros(self.n, dtype=bool)

        self.groups = _Groups()
        self._collect_groups(circuit)
        self._baseline = self._make_baselines(circuit)
        # Each ascending neuron samples the gait rhythm at its own phase, so the
        # proprioceptive drive is a travelling wave rather than one pulse.
        self._ascending_phase = self._rng.uniform(
            0.0, 2.0 * math.pi, size=len(self.groups.ascending)
        )
        self._build_weights(circuit)

        # Inputs, written by the coordinator each frame.
        self.loom_left = 0.0
        self.loom_right = 0.0
        self.gait_drive = 0.0
        self.gait_phase = 0.0
        self.air_puff = 0.0
        self.activity_scale = 1.0  # circadian and sleep neuromodulation
        self.sensory_gate = 1.0  # sleep raises the arousal threshold

        # Outputs: hertz per neuron, exponentially smoothed.
        self._rates = np.zeros(RATE_GROUPS, dtype=np.float64)
        self._rate_counts = np.zeros(RATE_GROUPS, dtype=np.float64)
        self.rate_population = 0.0
        self._gf_latch = False
        self.sim_ms = 0
        self.total_spikes = 0

        self._burst_until = 0
        self._burst_next = k.BURST_FIRST_MS

        self._stim_lock = threading.Lock()
        self._pending_stims: list[_Stim] = []
        self._active_stims: list[_Stim] = []

    # -- construction -------------------------------------------------------

    def _collect_groups(self, circuit: Circuit) -> None:
        buckets: dict[str, list[int]] = {name: [] for name in vars(self.groups)}
        for i, neuron in enumerate(circuit.neurons):
            role, side = neuron.role, neuron.side
            if role in ("lc4", "lplc2"):
                buckets["loom_left" if side == "left" else "loom_right"].append(i)
            elif role == "gf":
                buckets["gf"].append(i)
            elif role in ("dna01", "dna02"):
                buckets["dna_left" if side == "left" else "dna_right"].append(i)
            elif role == "mdn":
                buckets["mdn"].append(i)
            elif role == "dnp09":
                buckets["forward"].append(i)
            elif role == "dng11":
                buckets["groom"].append(i)
            elif role == "escw":
                buckets["escape_wing"].append(i)
            elif role == "other":
                # Partner neurons keep their FlyWire super-class as their type.
                if neuron.type == "ascending":
                    buckets["ascending"].append(i)
                elif neuron.type == "sensory":
                    buckets["sensory"].append(i)
        for name, indices in buckets.items():
            setattr(self.groups, name, np.asarray(indices, dtype=np.int64))

        self._is_gf = np.zeros(self.n, dtype=bool)
        self._is_gf[self.groups.gf] = True

        # Which rate readout each neuron contributes to, or -1 for none.
        self._rate_group = np.full(self.n, -1, dtype=np.int64)
        for indices, slot in (
            (self.groups.loom_left, RATE_LOOM),
            (self.groups.loom_right, RATE_LOOM),
            (self.groups.dna_left, RATE_DNA_L),
            (self.groups.dna_right, RATE_DNA_R),
            (self.groups.mdn, RATE_MDN),
            (self.groups.forward, RATE_FORWARD),
            (self.groups.groom, RATE_GROOM),
            (self.groups.escape_wing, RATE_ESCAPE_WING),
        ):
            self._rate_group[indices] = slot
        sizes = np.maximum(
            np.bincount(self._rate_group[self._rate_group >= 0], minlength=RATE_GROUPS), 1
        ).astype(np.float64)
        # One spike in one millisecond is 1000 Hz; divide by the population size.
        self._rate_hz_scale = 1000.0 / sizes

    def _make_baselines(self, circuit: Circuit) -> npt.NDArray[np.float64]:
        baseline = np.empty(self.n, dtype=np.float64)
        for i, neuron in enumerate(circuit.neurons):
            role = neuron.role
            if role == "other":
                baseline[i] = self._rng.uniform(*k.BASELINE_OTHER)
            elif role in ("lc4", "lplc2"):
                baseline[i] = k.BASELINE_LOOM
            elif role in ("dna01", "dna02", "mdn", "dng11", "escw"):
                baseline[i] = k.BASELINE_COMMAND
            elif role == "dnp09":
                baseline[i] = k.BASELINE_FORWARD
            else:
                baseline[i] = k.BASELINE_QUIET
        return baseline

    def _build_weights(self, circuit: Circuit) -> None:
        """Two dense presynaptic-row weight matrices, excitatory and inhibitory.

        Upstream stores the graph as CSR because Swift walks it edge by edge.
        Here the delivery step is `W[spiked].sum(axis=0)`, so a dense matrix is
        both faster and shorter than a ragged gather: 668x668 float32 is 1.8 MB
        per matrix, which is nothing, and only the rows that actually spiked are
        ever touched. The arithmetic is identical.
        """
        edges = circuit.edges
        pre = edges[:, 0].astype(np.int64)
        post = edges[:, 1].astype(np.int64)
        weight = edges[:, 2].astype(np.float64) * k.WEIGHT_SCALE

        # The looming detectors and the wind-sensitive pathway reach the giant
        # fiber through electrical synapses, which a chemical synapse count
        # under-represents. Boost exactly those edges.
        is_electrical = np.asarray(
            [
                role in ("lc4", "lplc2") or (role == "other" and typ == "sensory")
                for role, typ in zip(self.roles, self.types, strict=True)
            ]
        )
        boosted = is_electrical[pre] & self._is_gf[post]
        weight[boosted] *= k.GAP_JUNCTION_BOOST

        self._w_excitatory = np.zeros((self.n, self.n), dtype=np.float32)
        self._w_inhibitory = np.zeros((self.n, self.n), dtype=np.float32)
        excitatory = weight >= 0
        np.add.at(self._w_excitatory, (pre[excitatory], post[excitatory]), weight[excitatory])
        np.add.at(self._w_inhibitory, (pre[~excitatory], post[~excitatory]), weight[~excitatory])
        # Most neurons inhibit nobody; checking that is cheaper than summing a
        # row of zeros into the delay queue every millisecond.
        self._inhibits = self._w_inhibitory.any(axis=1)

    # -- public API ---------------------------------------------------------

    def stimulate(self, indices: npt.NDArray[np.int64], strength: float, duration_ms: int) -> None:
        """Inject current into a set of neurons. Safe to call from any thread."""
        if len(indices) == 0:
            return
        with self._stim_lock:
            self._pending_stims.append(_Stim(np.asarray(indices, np.int64), strength, duration_ms))
            if len(self._pending_stims) > k.STIM_PENDING_LIMIT:
                del self._pending_stims[0]

    def consume_gf(self) -> bool:
        """True if the giant fiber spiked since the last call, and clears the latch."""
        latched = self._gf_latch
        self._gf_latch = False
        return latched

    @property
    def rate_loom(self) -> float:
        return float(self._rates[RATE_LOOM])

    @property
    def rate_dna_left(self) -> float:
        return float(self._rates[RATE_DNA_L])

    @property
    def rate_dna_right(self) -> float:
        return float(self._rates[RATE_DNA_R])

    @property
    def rate_mdn(self) -> float:
        return float(self._rates[RATE_MDN])

    @property
    def rate_forward(self) -> float:
        return float(self._rates[RATE_FORWARD])

    @property
    def rate_groom(self) -> float:
        return float(self._rates[RATE_GROOM])

    @property
    def rate_escape_wing(self) -> float:
        return float(self._rates[RATE_ESCAPE_WING])

    def step(self, ms: int) -> None:
        """Integrate `ms` milliseconds. Port of Sim.swift LIFSim.step."""
        if ms <= 0:
            return
        self._merge_pending_stims()

        # The neuromodulation inputs are written once per frame, so the resting
        # drive is constant across this call and is folded in here rather than
        # recomputed 1000 times a second.
        np.multiply(self._baseline, self.activity_scale, out=self._drive)

        sampled: list[SpikeEvent] = []
        remaining = ms
        while remaining > 0:
            # Random draws come in blocks: one numpy call per block instead of
            # one per millisecond, which is the single biggest cost at this size.
            block = min(remaining, NOISE_BLOCK_MS)
            noise_draw = self._rng.random((block, self.n))
            remaining -= block
            for row in range(block):
                self.sim_ms += 1
                self._advance_burst()
                self._integrate_one_ms(noise_draw[row])
                spiked = self._detect_spikes()
                self._deliver(spiked)
                self._update_rates(spiked)
                if self.spike_bus is not None and len(spiked):
                    sampled.extend(self._sample_spikes(spiked))

        if self.spike_bus is not None:
            self.spike_bus.push(sampled)

    # -- one millisecond ----------------------------------------------------

    def _merge_pending_stims(self) -> None:
        with self._stim_lock:
            pending, self._pending_stims = self._pending_stims, []
        for stim in pending:
            stim.until_ms = self.sim_ms + stim.duration_ms
            self._active_stims.append(stim)
        self._active_stims = [s for s in self._active_stims if self.sim_ms < s.until_ms]

    def _advance_burst(self) -> None:
        if self.sim_ms >= self._burst_next:
            self._burst_until = self.sim_ms + k.BURST_DURATION_MS
            self._burst_next = self.sim_ms + int(self._rng.integers(*k.BURST_INTERVAL_MS))

    def _integrate_one_ms(self, noise_draw: npt.NDArray[np.float64]) -> None:
        v, refractory = self._v, self._refractory
        bursting = self.sim_ms < self._burst_until
        noise_p = k.NOISE_PROBABILITY * (k.BURST_NOISE_FACTOR if bursting else 1.0)
        noise_p *= self.activity_scale

        # Refractory neurons only leak: they take no baseline drive and no noise.
        # Synaptic and sensory input below still reaches them, as upstream.
        v *= k.MEMBRANE_DECAY
        np.less(noise_draw, noise_p, out=self._noise_hit)
        np.multiply(self._noise_hit, k.NOISE_KICK, out=self._scratch)
        self._scratch += self._drive
        np.greater(refractory, 0.0, out=self._refractory_hit)
        np.logical_not(self._refractory_hit, out=self._resting)
        np.add(v, self._scratch, out=v, where=self._resting)
        np.subtract(refractory, 1.0, out=refractory, where=self._refractory_hit)
        # A neuron whose countdown just reached zero is no longer refractory.
        np.greater(refractory, 0.0, out=self._refractory_hit)

        groups = self.groups
        if self.loom_left > 0.001:
            v[groups.loom_left] += self.loom_left * k.LOOM_GAIN * self.sensory_gate
        if self.loom_right > 0.001:
            v[groups.loom_right] += self.loom_right * k.LOOM_GAIN * self.sensory_gate
        if self.gait_drive > 0.001:
            # Body to brain: the gait rhythm feeds the real ascending neurons in
            # phase with the legs, closing the loop the connectome describes.
            phase = self.gait_phase * 2.0 * math.pi
            wave = 0.5 + 0.5 * np.sin(phase + self._ascending_phase)
            v[groups.ascending] += self.gait_drive * k.GAIT_GAIN * wave
        if self.air_puff > 0.001:
            v[groups.sensory] += self.air_puff * k.AIR_PUFF_GAIN * self.sensory_gate
        for stim in self._active_stims:
            v[stim.indices] += stim.strength

        # Inhibition scheduled for this millisecond arrives now.
        slot = self._inhibitory_queue[self._queue_head]
        if self._queue_loaded[self._queue_head]:
            v += slot
            np.maximum(v, k.MEMBRANE_FLOOR, out=v)
            slot.fill(0.0)
            self._queue_loaded[self._queue_head] = False

    def _detect_spikes(self) -> npt.NDArray[np.int64]:
        np.greater_equal(self._v, k.SPIKE_THRESHOLD, out=self._spike_hit)
        # _refractory_hit still holds the pre-decrement mask, so a neuron whose
        # refractory period ends this millisecond is eligible again, as upstream.
        self._spike_hit &= ~self._refractory_hit
        spiked = np.flatnonzero(self._spike_hit)
        if len(spiked):
            self._v[spiked] = 0.0
            self._refractory[spiked] = k.REFRACTORY_MS
            self.total_spikes += len(spiked)
            if self._is_gf[spiked].any():
                self._gf_latch = True
        return spiked

    def _deliver(self, spiked: npt.NDArray[np.int64]) -> None:
        """Push each spike along its real outgoing synapses."""
        if len(spiked):
            self._v += self._w_excitatory[spiked].sum(axis=0)
            np.maximum(self._v, k.MEMBRANE_FLOOR, out=self._v)
            if self._inhibits[spiked].any():
                slot = (self._queue_head + k.INHIBITORY_DELAY_MS) % k.INHIBITORY_QUEUE_SLOTS
                self._inhibitory_queue[slot] += self._w_inhibitory[spiked].sum(axis=0)
                self._queue_loaded[slot] = True
        self._queue_head = (self._queue_head + 1) % k.INHIBITORY_QUEUE_SLOTS

    def _update_rates(self, spiked: npt.NDArray[np.int64]) -> None:
        counts = self._rate_counts
        counts.fill(0.0)
        if len(spiked):
            slots = self._rate_group[spiked]
            slots = slots[slots >= 0]
            if len(slots):
                counts[:] = np.bincount(slots, minlength=RATE_GROUPS)
        self._rates += (counts * self._rate_hz_scale - self._rates) * k.RATE_ALPHA
        population = len(spiked) * 1000.0 / self.n
        self.rate_population += (population - self.rate_population) * k.RATE_ALPHA

    def _sample_spikes(self, spiked: npt.NDArray[np.int64]) -> list[SpikeEvent]:
        # Under heavy activity only a sample is shown, or the flash pool churns
        # faster than the eye can follow.
        stride = max(1, len(spiked) // k.SPIKE_SAMPLE_TARGET)
        return [SpikeEvent(int(i), bool(self._is_gf[i])) for i in spiked[::stride]]
