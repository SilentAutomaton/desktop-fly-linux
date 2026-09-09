"""Loading the shipped connectome data files.

Port of Sim.swift findDataDir / loadBrainData. The files themselves are
upstream's, byte-identical, and carry two different licences: the FlyWire brain
files are CC BY-NC 4.0 and the MaleCNS nerve-cord file is CC BY 4.0. See
data/DATA_LICENSE.md and data/LOCOMOTOR_PROVENANCE.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

BRAIN_POINTS_FILE = "brain_points.json"
CIRCUIT_FILE = "circuit.json"
LOCOMOTOR_FILE = "locomotor_circuit.json"

# The order the six legs appear in throughout the data, the simulation and the
# body: right and left front, middle, hind. Written down because a leg index is
# meaningless without it.
LEG_ORDER = ("RF", "LF", "RM", "LM", "RH", "LH")
LEG_COUNT = len(LEG_ORDER)

# The four antagonist channels every leg must have for the circuit to be able to
# drive a step at all. validate() refuses a file that is missing any of them.
REQUIRED_MOTOR_CHANNELS = (
    "tibia_flexor",
    "tibia_extensor",
    "trochanter_flexor",
    "trochanter_extensor",
)


@dataclass(frozen=True)
class BrainPoints:
    """The 23k soma cloud drawn by the brain window."""

    classes: list[str]  # FlyWire super-class names, indexed by the 4th column
    positions: npt.NDArray[np.float32]  # (n, 3) normalised into roughly [-10, 10]
    class_index: npt.NDArray[np.int32]  # (n,)


@dataclass(frozen=True)
class CircuitNeuron:
    id: str  # FlyWire root id
    type: str  # primary cell type, or the super-class for partner neurons
    role: str  # lc4 | lplc2 | gf | dna01 | dna02 | dnp09 | dng11 | mdn | escw | other
    side: str  # left | right | center
    pos: tuple[float, float, float]


@dataclass(frozen=True)
class Circuit:
    neurons: list[CircuitNeuron]
    edges: npt.NDArray[np.float32]  # (m, 3): pre index, post index, signed synapse count


@dataclass(frozen=True)
class LocomotorNeuron:
    """One MaleCNS nerve-cord neuron. Port of Sim.swift LocomotorNeuronFile.

    The shipped file carries far more per neuron - source annotations, soma
    coordinates, transmitter predictions and their confidences - so that a
    different physiological model can be built on the same extraction. The
    simulation reads only these fields, exactly as upstream does.
    """

    id: str  # MaleCNS body id
    type: str  # cell type, e.g. DNp09
    role: str  # descending | premotor | motor | sensory | ascending
    side: str  # left | right | center | unknown
    leg: int | None  # index into LEG_ORDER, or None for cells with no leg
    motor_channel: str | None  # named muscle action, motor cells only
    sensory_kind: str | None  # chordotonal | campaniform | hair_plate | ...


@dataclass(frozen=True)
class LocomotorCircuit:
    neurons: list[LocomotorNeuron]
    edges: npt.NDArray[np.float32]  # (m, 3): pre index, post index, signed contact count

    def validate(self) -> bool:
        """Port of Sim.swift LocomotorCircuitFile.validate.

        A malformed file must be refused rather than half-loaded: the fly
        degrades to the scripted gait, which is a supported state, whereas a
        circuit missing one antagonist channel would silently produce a leg that
        can flex and never extend.
        """
        if not self.neurons or len(self.edges) == 0:
            return False
        if len({n.id for n in self.neurons}) != len(self.neurons):
            return False
        if any(n.leg is not None and not 0 <= n.leg < LEG_COUNT for n in self.neurons):
            return False
        if self.edges.shape[1] != 3 or not np.isfinite(self.edges).all():
            return False
        index = self.edges[:, :2]
        if (index < 0).any() or (index >= len(self.neurons)).any():
            return False
        if (index != np.rint(index)).any():
            return False
        channels = {(n.leg, n.motor_channel) for n in self.neurons}
        return all(
            (leg, channel) in channels
            for leg in range(LEG_COUNT)
            for channel in REQUIRED_MOTOR_CHANNELS
        )


def data_dir_candidates() -> list[Path]:
    """Every place the data files may live, in priority order.

    No path is hardcoded past the XDG defaults the specification itself
    defines, so a packaged install, a checkout and a user override all work.
    """
    here = Path(__file__).resolve().parent
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    home_data = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"

    candidates = [
        home_data / "desktop-fly" / "data",
        here.parent / "data",  # a git checkout: <repo>/data next to the package
        here / "data",  # a wheel that bundled the files inside the package
        Path.cwd() / "data",  # upstream's behaviour: data next to the working directory
    ]
    for base in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if base:
            candidates.append(Path(base) / "desktop-fly" / "data")
    return candidates


def find_data_dir() -> Path | None:
    for path in data_dir_candidates():
        if (path / CIRCUIT_FILE).is_file() and (path / BRAIN_POINTS_FILE).is_file():
            return path
    return None


def load_brain_points(path: Path) -> BrainPoints:
    raw = json.loads((path / BRAIN_POINTS_FILE).read_text())
    points = np.asarray(raw["points"], dtype=np.float32)
    return BrainPoints(
        classes=list(raw["classes"]),
        positions=np.ascontiguousarray(points[:, :3]),
        class_index=points[:, 3].astype(np.int32),
    )


def load_circuit(path: Path) -> Circuit:
    raw = json.loads((path / CIRCUIT_FILE).read_text())
    neurons = [
        CircuitNeuron(
            id=n["id"],
            type=n["type"],
            role=n["role"],
            side=n["side"],
            pos=(float(n["pos"][0]), float(n["pos"][1]), float(n["pos"][2])),
        )
        for n in raw["neurons"]
    ]
    return Circuit(neurons=neurons, edges=np.asarray(raw["edges"], dtype=np.float32))


def load_locomotor(path: Path) -> LocomotorCircuit | None:
    """The MaleCNS nerve cord, or None if it is absent or malformed.

    Returning None is a supported state, not an error: the fly falls back to the
    scripted gait and the sinusoidal proprioceptive drive it used before v1.1.0.
    """
    file = path / LOCOMOTOR_FILE
    if not file.is_file():
        return None
    raw = json.loads(file.read_text())
    circuit = LocomotorCircuit(
        neurons=[
            LocomotorNeuron(
                id=n["id"],
                type=n["type"],
                role=n["role"],
                side=n["side"],
                leg=n["leg"],
                motor_channel=n["motorChannel"],
                sensory_kind=n["sensoryKind"],
            )
            for n in raw["neurons"]
        ],
        edges=np.asarray(raw["edges"], dtype=np.float32),
    )
    return circuit if circuit.validate() else None


@dataclass(frozen=True)
class BrainData:
    directory: Path
    points: BrainPoints
    circuit: Circuit
    locomotor: LocomotorCircuit | None

    def describe(self) -> str:
        """The provenance line shown in the tray menu, as upstream shows it."""
        line = (
            f"FlyWire v783 · {len(self.points.positions)} somas · "
            f"circuit {len(self.circuit.neurons)}n/{len(self.circuit.edges)}e"
        )
        if self.locomotor is not None:
            line += (
                f" · MaleCNS {len(self.locomotor.neurons)}n/{len(self.locomotor.edges)}e"
            )
        return line


def load_brain_data() -> BrainData | None:
    """Returns None when no data directory is present, exactly as upstream does.

    A missing dataset is not an error: the app falls back to the legacy
    distance-based behaviour and says so.
    """
    directory = find_data_dir()
    if directory is None:
        return None
    return BrainData(
        directory=directory,
        points=load_brain_points(directory),
        circuit=load_circuit(directory),
        locomotor=load_locomotor(directory),
    )
