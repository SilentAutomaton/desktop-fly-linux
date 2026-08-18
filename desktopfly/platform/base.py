"""The complete list of things this program needs from an operating system.

It is short on purpose. Porting to another platform means implementing these
six sources and nothing else; see DESIGN.md section 8 for the Windows mapping.

Every source reports `available` and `describe()`, which is what --probe
prints, and a source that cannot work on the running system must degrade to
unavailable rather than raise. Missing senses make the fly less informed, never
broken: with no pointer it still runs on window looms, taps, the circadian
rhythm and the network's own noise.

The plain dataclasses these interfaces trade in live in desktopfly.environment
and are re-exported here, so the core never has to import the platform layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from desktopfly.environment import Activity, OutputInfo, WindowRect

__all__ = [
    "Activity",
    "Backends",
    "NullPointerSource",
    "NullTapSource",
    "NullThermalSource",
    "NullWindowSource",
    "OutputInfo",
    "OutputSource",
    "Overlay",
    "PointerSource",
    "Source",
    "TapSource",
    "ThermalSource",
    "WindowRect",
    "WindowSource",
]


class Source(ABC):
    """Common shape of every sense: it either works, or says why it does not."""

    available: bool = False

    @abstractmethod
    def describe(self) -> str:
        """One line for --probe: what this is, and what it currently reads."""


class Overlay(ABC):
    """A full-output, click-through, always-on-top surface with a GL context."""

    @abstractmethod
    def create(self, output: OutputInfo, on_frame: Callable[[float], None]) -> None:
        """Map the surface on `output` and call `on_frame(dt)` every frame."""

    @abstractmethod
    def geometry(self) -> tuple[int, int]:
        """Drawable size in pixels, which is the logical size times the scale."""

    @abstractmethod
    def move_to(self, output: OutputInfo) -> None:
        """Rebind to another output. Upstream's 'Move to Next Display'."""

    @abstractmethod
    def destroy(self) -> None: ...


class OutputSource(Source):
    @abstractmethod
    def outputs(self) -> list[OutputInfo]: ...

    @abstractmethod
    def current(self) -> OutputInfo | None:
        """The output the fly should live on, usually the focused one."""


class WindowSource(Source):
    @abstractmethod
    def rects(self) -> list[WindowRect]:
        """Other applications' windows, in compositor space.

        Only the rectangles: the size filters, the ledge extraction and the
        appeared-since-last-poll diff belong to environment.WindowSense, so
        that supporting one more compositor stays a small file.
        """


class PointerSource(Source):
    @abstractmethod
    def position(self) -> tuple[float, float] | None:
        """Global cursor position, or None where the platform forbids asking."""


class TapSource(Source):
    @abstractmethod
    def drain(self) -> Activity:
        """Input activity since the previous call: how many, and when.

        Never which key. This is the same "when, never what" guarantee upstream
        makes, and it is what the fly's substrate-tap and vibration senses need.
        """


class ThermalSource(Source):
    @abstractmethod
    def load(self) -> float | None:
        """CPU heat as 0..1, normalised against the sensor's own limit."""


# -- degradations ------------------------------------------------------------
# Named rather than anonymous, so --probe can say which sense is missing.


class NullWindowSource(WindowSource):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def rects(self) -> list[WindowRect]:
        return []

    def describe(self) -> str:
        return f"windows: unavailable ({self.reason})"


class NullPointerSource(PointerSource):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def position(self) -> tuple[float, float] | None:
        return None

    def describe(self) -> str:
        return f"pointer: unavailable ({self.reason}) — the fly runs blind"


class NullTapSource(TapSource):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def drain(self) -> Activity:
        return Activity()

    def describe(self) -> str:
        return f"taps: unavailable ({self.reason})"


class NullThermalSource(ThermalSource):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def load(self) -> float | None:
        return None

    def describe(self) -> str:
        return f"thermal: unavailable ({self.reason}) — constant tempo"


class Backends:
    """The set of sources chosen for the running system."""

    def __init__(
        self,
        name: str,
        outputs: OutputSource,
        windows: WindowSource,
        pointer: PointerSource,
        taps: TapSource,
        thermal: ThermalSource,
    ) -> None:
        self.name = name
        self.outputs = outputs
        self.windows = windows
        self.pointer = pointer
        self.taps = taps
        self.thermal = thermal

    def describe(self) -> list[str]:
        return [f"session: {self.name}"] + [
            source.describe()
            for source in (self.outputs, self.windows, self.pointer, self.taps, self.thermal)
        ]
