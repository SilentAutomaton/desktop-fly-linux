"""Loading the shipped FlyWire data files.

Port of Sim.swift findDataDir / loadBrainData. The files themselves are
upstream's, byte-identical, and are CC BY-NC 4.0 — see data/DATA_LICENSE.md.
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


@dataclass(frozen=True)
class BrainData:
    directory: Path
    points: BrainPoints
    circuit: Circuit

    def describe(self) -> str:
        """The provenance line shown in the tray menu, as upstream shows it."""
        return (
            f"FlyWire v783 · {len(self.points.positions)} somas · "
            f"circuit {len(self.circuit.neurons)}n/{len(self.circuit.edges)}e"
        )


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
    )
