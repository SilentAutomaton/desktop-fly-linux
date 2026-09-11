"""The MaleCNS nerve cord: descending commands in, muscle activations out.

Port of Locomotor.swift. A second connectome from a second animal — 1045
neurons and 17224 measured connections of the male ventral nerve cord — sitting
between the FlyWire brain and the six legs.

The anatomy and the contact counts are measured. The LIF parameters, the rate
transfer between the two specimens, the sensory tuning and the muscle
activation are modelling assumptions, and data/LOCOMOTOR_PROVENANCE.md is the
authority on exactly where that line falls. A working anatomical path validates
the extraction; it does not validate biological motion.

There are no gait oscillators here and no neuron-ID-derived tuning. Input has
to recruit the real graph, and what comes out is whatever the graph does.

Vectorised the same way sim.py is: upstream walks the neurons in a Swift loop
each millisecond, this walks the same millisecond as a handful of numpy
operations. Upstream delivers each spike into next-millisecond buffers, so
delivery is synchronous and the order inside a millisecond cannot matter, which
is what makes the two forms identical rather than merely similar.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from . import constants as k
from .dataset import LEG_COUNT, LocomotorCircuit
from .legdynamics import HIP_LIMIT, REST_KNEE, LegFeedback, LegMotorCommand

# Which muscle channels drive which body axis. The named coxa promotors occur
# only on the front legs in this extraction, and all six legs carry sternal
# rotators; the anatomical supplement identifies those rotators with
# anterior/posterior coxal movement. Collapsing rotation and promotion onto one
# body axis is a mechanical simplification of the body model — it does not
# relabel a rotator as a measured promotor neuron.
PROTRACT_CHANNELS = ("coxa_promotor", "coxa_anterior_rotator")
RETRACT_CHANNELS = ("coxa_remotor", "coxa_posterior_rotator")
LIFT_CHANNEL = "trochanter_flexor"
DEPRESS_CHANNEL = "trochanter_extensor"
FLEX_CHANNEL = "tibia_flexor"
EXTEND_CHANNEL = "tibia_extensor"

# How each sensory class is transduced. Direction tuning is not annotated, so
# it is pooled: proprioceptors encode joint excursion and speed, contact
# sensors encode load. Upstream also names a "contact" kind here; the shipped
# extraction never uses that string, and it is handled with the campaniform
# sensilla either way.
LOAD_KINDS = ("campaniform", "contact")
HAIR_KIND = "hair_plate"


class LocomotorSim:
    """1045 real nerve-cord neurons, integrated at 1 kHz alongside the brain."""

    def __init__(self, circuit: LocomotorCircuit):
        if not circuit.validate():
            raise ValueError("invalid MaleCNS locomotor circuit")
        self.circuit = circuit
        self.n = len(circuit.neurons)

        self._voltage = np.zeros(self.n, dtype=np.float64)
        self._adaptation = np.zeros(self.n, dtype=np.float64)
        self._refractory = np.zeros(self.n, dtype=np.float64)
        self._rates = np.zeros(self.n, dtype=np.float64)
        self._excitatory = np.zeros(self.n, dtype=np.float64)
        self._inhibitory = np.zeros(self.n, dtype=np.float64)
        self._next_excitatory = np.zeros(self.n, dtype=np.float64)
        self._next_inhibitory = np.zeros(self.n, dtype=np.float64)
        self._drive = np.zeros(self.n, dtype=np.float64)
        self._sensory_drive = np.zeros(self.n, dtype=np.float64)

        self._build_weights(circuit)
        self._collect_groups(circuit)

        self.commands = [LegMotorCommand() for _ in range(LEG_COUNT)]
        self._feedback: list[LegFeedback] = []
        self._transduced: object = None
        self.total_spikes = 0
        self.motor_spikes = 0
        self.sensory_spikes = 0
        self.sim_ms = 0

        # Lesions are diagnostic interventions, used by the locomotor suite to
        # verify that a movement travelled the causal path it claims to.
        self.silenced: npt.NDArray[np.int64] = np.empty(0, dtype=np.int64)
        self.synapses_enabled = True
        self.feedback_enabled = True

    # -- construction -------------------------------------------------------

    def _build_weights(self, circuit: LocomotorCircuit) -> None:
        edges = circuit.edges
        pre = edges[:, 0].astype(np.int64)
        post = edges[:, 1].astype(np.int64)
        count = edges[:, 2].astype(np.float64)

        input_total = np.zeros(self.n, dtype=np.float64)
        np.add.at(input_total, post, np.abs(count))
        weight = (
            k.LOCOMOTOR_SYNAPTIC_GAIN
            * count
            / np.maximum(k.LOCOMOTOR_INPUT_FLOOR, input_total[post])
        )

        self._w_excitatory = np.zeros((self.n, self.n), dtype=np.float32)
        self._w_inhibitory = np.zeros((self.n, self.n), dtype=np.float32)
        excitatory = weight >= 0
        np.add.at(self._w_excitatory, (pre[excitatory], post[excitatory]), weight[excitatory])
        np.add.at(self._w_inhibitory, (pre[~excitatory], post[~excitatory]), weight[~excitatory])

    def _collect_groups(self, circuit: LocomotorCircuit) -> None:
        self._command_groups: dict[tuple[str, str], npt.NDArray[np.int64]] = {}
        motor: dict[tuple[int, str], list[int]] = {}
        commands: dict[tuple[str, str], list[int]] = {}
        sensory: list[int] = []
        for i, neuron in enumerate(circuit.neurons):
            if neuron.role == "descending":
                commands.setdefault((neuron.type, neuron.side), []).append(i)
            elif neuron.role == "sensory" and neuron.leg is not None:
                sensory.append(i)
            elif neuron.role == "motor" and neuron.leg is not None and neuron.motor_channel:
                motor.setdefault((neuron.leg, neuron.motor_channel), []).append(i)
        self._command_groups = {key: np.asarray(v, np.int64) for key, v in commands.items()}

        # The 48 motor channel means are read every simulated millisecond, so
        # they are gathered as one gather plus one segmented sum rather than as
        # 48 separate reductions over six-element index arrays.
        self._channel_slot = {key: slot for slot, key in enumerate(motor)}
        sizes = [len(v) for v in motor.values()]
        self._channel_index = np.concatenate(
            [np.asarray(v, np.int64) for v in motor.values()]
        )
        self._channel_start = np.concatenate(([0], np.cumsum(sizes)[:-1])).astype(np.int64)
        self._channel_size = np.asarray(sizes, dtype=np.float64)

        self._roles = np.asarray([n.role for n in circuit.neurons])
        self._legs = np.asarray([-1 if n.leg is None else n.leg for n in circuit.neurons])
        self._is_motor = self._roles == "motor"
        self._is_sensory = self._roles == "sensory"

        # The sensory population, split once into the three transduction classes
        # so the per-millisecond work is three array writes.
        self._sensory = np.asarray(sensory, np.int64)
        kinds = [circuit.neurons[i].sensory_kind for i in sensory]
        self._sensory_leg = self._legs[self._sensory]
        # The dtype is explicit because an empty sensory population would make
        # these float64 arrays, and the `|` below is a bitwise op.
        self._sensory_load: npt.NDArray[np.bool_] = np.asarray(
            [kind in LOAD_KINDS for kind in kinds], np.bool_
        )
        self._sensory_hair: npt.NDArray[np.bool_] = np.asarray(
            [kind == HAIR_KIND for kind in kinds], np.bool_
        )
        self._sensory_joint = ~(self._sensory_load | self._sensory_hair)

    # -- public API ---------------------------------------------------------

    @property
    def feedback(self) -> list[LegFeedback]:
        return self._feedback

    @feedback.setter
    def feedback(self, value: list[LegFeedback]) -> None:
        self._feedback = value
        self._transduced = None  # the sensory drive it implies is now stale

    def set_descending(self, cell_type: str, side: str, rate: float) -> None:
        """Drive one descending cell type on one side at a population firing rate.

        This is the modelled homologous interface between the two specimens. It
        adds current to real male cells; it never fabricates a graph edge.
        """
        group = self._command_groups.get((cell_type, side))
        if group is not None:
            self._drive[group] = min(
                k.DESCENDING_DRIVE_LIMIT, max(0.0, rate) * k.DESCENDING_DRIVE_PER_HZ
            )

    def indices(self, role: str, leg: int | None = None) -> npt.NDArray[np.int64]:
        match = self._roles == role
        if leg is not None:
            match &= self._legs == leg
        return np.flatnonzero(match)

    def mean_rate(self, role: str, leg: int | None = None) -> float:
        group = self.indices(role, leg)
        return float(self._rates[group].mean()) if len(group) else 0.0

    def step(self, ms: int) -> None:
        """Integrate `ms` milliseconds. Port of Locomotor.swift LocomotorSim.step."""
        if ms <= 0:
            return
        # The transduction upstream recomputes every millisecond depends only on
        # the feedback, which cannot change inside a call. Recomputing it once
        # per new sample gives identical values for a fraction of the work.
        if self._transduced is not self._feedback:
            self._transduce_sensory()
            self._transduced = self._feedback
        for _ in range(ms):
            self.sim_ms += 1
            self._integrate_one_ms()
        self._read_motor_commands()

    # -- one millisecond ----------------------------------------------------

    def _transduce_sensory(self) -> None:
        self._sensory_drive.fill(0.0)
        if not self.feedback_enabled or len(self.feedback) != LEG_COUNT:
            return
        legs = self._sensory_leg
        contact = np.asarray([f.contact for f in self.feedback])[legs]
        load = np.asarray([f.load for f in self.feedback])[legs]
        hip = np.abs(np.asarray([f.hip_angle for f in self.feedback])[legs])
        hip_velocity = np.abs(np.asarray([f.hip_velocity for f in self.feedback])[legs])
        knee = np.asarray([f.knee_angle for f in self.feedback])[legs]
        knee_velocity = np.abs(np.asarray([f.knee_velocity for f in self.feedback])[legs])
        elevation_velocity = np.abs(
            np.asarray([f.elevation_velocity for f in self.feedback])[legs]
        )

        value = np.minimum(
            1.0,
            knee_velocity / k.SENSORY_KNEE_VELOCITY_SCALE
            + hip_velocity / k.SENSORY_HIP_VELOCITY_SCALE
            + np.abs(knee - REST_KNEE) * k.SENSORY_KNEE_EXCURSION_GAIN,
        )
        value = np.where(
            self._sensory_hair,
            np.minimum(
                1.0, hip / HIP_LIMIT + elevation_velocity / k.SENSORY_HAIR_VELOCITY_SCALE
            ),
            value,
        )
        value = np.where(
            self._sensory_load,
            np.where(contact, np.minimum(1.0, load * k.SENSORY_LOAD_GAIN), 0.0),
            value,
        )
        self._sensory_drive[self._sensory] = value * k.SENSORY_DRIVE_GAIN

    def _integrate_one_ms(self) -> None:
        # Finite synaptic currents. These timescales are model parameters; they
        # were not measured for the reconstructed specimen.
        self._excitatory *= k.LOCOMOTOR_EXCITATORY_DECAY
        self._excitatory += self._next_excitatory
        self._inhibitory *= k.LOCOMOTOR_INHIBITORY_DECAY
        self._inhibitory += self._next_inhibitory
        self._next_excitatory.fill(0.0)
        self._next_inhibitory.fill(0.0)

        self._rates *= k.LOCOMOTOR_RATE_DECAY
        self._adaptation *= k.LOCOMOTOR_ADAPTATION_DECAY
        if len(self.silenced):
            self._voltage[self.silenced] = 0.0
            self._rates[self.silenced] = 0.0

        refractory = self._refractory > 0
        resting = ~refractory
        if len(self.silenced):
            resting = resting.copy()
            resting[self.silenced] = False
            refractory = refractory.copy()
            refractory[self.silenced] = False
        np.subtract(self._refractory, 1.0, out=self._refractory, where=refractory)

        # Deterministic subthreshold excitability: no autonomous noise, so any
        # activity here was recruited through the real graph.
        integrated = np.maximum(
            k.LOCOMOTOR_MEMBRANE_FLOOR,
            self._voltage * k.LOCOMOTOR_MEMBRANE_DECAY
            + self._excitatory
            + self._inhibitory
            + k.LOCOMOTOR_BASELINE
            + self._drive
            + self._sensory_drive
            - self._adaptation,
        )
        np.copyto(self._voltage, integrated, where=resting)

        spiked = np.flatnonzero(resting & (self._voltage >= 1.0))
        if not len(spiked):
            return
        self._voltage[spiked] = 0.0
        self._refractory[spiked] = k.LOCOMOTOR_REFRACTORY_MS
        self._adaptation[spiked] += k.LOCOMOTOR_ADAPTATION_KICK
        self._rates[spiked] += k.LOCOMOTOR_RATE_KICK
        self.total_spikes += len(spiked)
        self.motor_spikes += int(self._is_motor[spiked].sum())
        self.sensory_spikes += int(self._is_sensory[spiked].sum())
        if self.synapses_enabled:
            self._next_excitatory += self._w_excitatory[spiked].sum(axis=0)
            self._next_inhibitory += self._w_inhibitory[spiked].sum(axis=0)

    def _read_motor_commands(self) -> None:
        rates = (
            np.add.reduceat(self._rates[self._channel_index], self._channel_start)
            / self._channel_size
        )
        activations = rates / (rates + k.MOTOR_HALF_ACTIVATION_HZ)

        def activity(leg: int, channel: str) -> float:
            slot = self._channel_slot.get((leg, channel))
            return 0.0 if slot is None else float(activations[slot])

        for leg in range(LEG_COUNT):
            self.commands[leg] = LegMotorCommand(
                protract=max(activity(leg, c) for c in PROTRACT_CHANNELS),
                retract=max(activity(leg, c) for c in RETRACT_CHANNELS),
                lift=activity(leg, LIFT_CHANNEL),
                depress=activity(leg, DEPRESS_CHANNEL),
                flex=activity(leg, FLEX_CHANNEL),
                extend=activity(leg, EXTEND_CHANNEL),
            )
