"""Desktop events turned into things a fly can sense.

Port of Environment.swift plus the sense bookkeeping upstream keeps in
AppDelegate. This module stays free of any platform code: the backends in
desktopfly/platform hand it plain rectangles and counters, and it turns those
into ledges, looms, a circadian activity level and a sleep decision.

Two coordinate systems meet here, and only here:

  compositor space  global layout pixels, origin top-left, y down
  scene space       origin at the centre of the fly's output, y up, 1 unit = 1 px

Scene space is upstream's convention, so everything downstream is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as k


@dataclass(frozen=True)
class OutputInfo:
    """One monitor, in compositor space."""

    name: str
    x: int
    y: int
    width: int
    height: int
    scale: float = 1.0

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def to_scene(self, x: float, y: float) -> tuple[float, float]:
        cx, cy = self.center
        return (x - cx, cy - y)


@dataclass(frozen=True)
class WindowRect:
    """Another application's window, in compositor space."""

    key: int  # stable across polls, so a window can be recognised and followed
    x: int
    y: int
    width: int
    height: int
    output: str = ""


@dataclass(frozen=True)
class Activity:
    """Coarse input activity: how many, and when. Never which key."""

    taps: int = 0  # pointer button presses
    keys: int = 0  # key presses
    seconds_since_event: float = float("inf")


@dataclass(frozen=True)
class Ledge:
    """A walkable window top edge, in scene coordinates. Port of Environment.swift."""

    y: float
    x0: float
    x1: float
    key: int


@dataclass
class NewWindow:
    """A window that appeared since the previous poll: a looming object."""

    center: tuple[float, float]  # scene coordinates
    size: float


@dataclass
class WindowSnapshot:
    ledges: list[Ledge] = field(default_factory=list)
    new_windows: list[NewWindow] = field(default_factory=list)


class WindowSense:
    """Window rectangles to terrain and looms. Port of Environment.swift WindowSense.

    Every compositor backend only has to produce rectangles; the filtering,
    the ledge extraction and the appeared-since-last-poll diff live here, so
    supporting one more compositor stays a small file.
    """

    def __init__(self) -> None:
        self._known: set[int] = set()
        self._first_poll = True

    def poll(self, rects: list[WindowRect], output: OutputInfo) -> WindowSnapshot:
        snapshot = WindowSnapshot()
        seen: set[int] = set()
        min_width, min_height = k.WINDOW_MIN_SIZE
        half_width = output.width / 2.0
        half_height = output.height / 2.0

        for rect in rects:
            seen.add(rect.key)
            if rect.width < min_width or rect.height < min_height:
                continue
            if not self._intersects(rect, output):
                continue

            top_y = output.to_scene(0.0, rect.y)[1]
            x0 = max(output.to_scene(rect.x, 0.0)[0], -half_width + k.LEDGE_SIDE_MARGIN)
            x1 = min(output.to_scene(rect.x + rect.width, 0.0)[0], half_width - k.LEDGE_SIDE_MARGIN)
            # An edge flush with the screen edge leaves nowhere to stand.
            inside = (
                -half_height + k.LEDGE_SCREEN_MARGIN < top_y < half_height - k.LEDGE_SCREEN_MARGIN
            )
            if inside and x1 - x0 > k.LEDGE_MIN_WIDTH and len(snapshot.ledges) < k.LEDGE_LIMIT:
                snapshot.ledges.append(Ledge(y=top_y, x0=x0, x1=x1, key=rect.key))

            if not self._first_poll and rect.key not in self._known:
                center = output.to_scene(rect.x + rect.width / 2.0, rect.y + rect.height / 2.0)
                snapshot.new_windows.append(NewWindow(center, float(max(rect.width, rect.height))))

        self._known = seen
        self._first_poll = False
        return snapshot

    @staticmethod
    def _intersects(rect: WindowRect, output: OutputInfo) -> bool:
        return (
            rect.x < output.x + output.width
            and rect.x + rect.width > output.x
            and rect.y < output.y + output.height
            and rect.y + rect.height > output.y
        )


def circadian_activity(hour: float) -> float:
    """Drosophila activity over the day. Port of Environment.swift circadianActivity.

    Morning and evening peaks, a midday siesta, night quiescence. The result
    scales the network's resting drive, never multiplies it raw - see
    constants.CIRCADIAN_COMPRESSION for why that distinction matters.
    """
    curve = k.CIRCADIAN_CURVE
    for (h0, a0), (h1, a1) in zip(curve, curve[1:], strict=False):
        if h0 <= hour <= h1:
            t = (hour - h0) / max(0.001, h1 - h0)
            return a0 + (a1 - a0) * t
    return curve[0][1]


def is_sleepy(idle_seconds: float, hour: float) -> bool:
    """Upstream's rule: idle at night, or idle for a very long time at any hour."""
    return (idle_seconds > 600 and (hour >= 22 or hour < 6)) or idle_seconds > 1800


def thermal_tempo(heat: float | None) -> float:
    """Flies are ectotherms: a hot machine is a fast fly.

    `heat` is 0..1, normalised by the platform against the sensor's own critical
    threshold. None means the machine exposes no usable sensor, which is a
    supported state and simply means a constant tempo.
    """
    if heat is None:
        return 1.0
    return 1.0 + min(1.0, max(0.0, heat)) * (k.THERMAL_TEMPO_MAX - 1.0)


class TypingLevel:
    """Keystrokes smoothed into a substrate-vibration level, 0..1.

    Port of the typingLevel filter in AppDelegate. It knows when keys were
    pressed and never which, exactly as upstream's idle-time query does.
    """

    def __init__(self) -> None:
        self.value = 0.0

    def update(self, seconds_since_key: float) -> float:
        target = 1.0 if seconds_since_key < k.TYPING_WINDOW_S else 0.0
        self.value += (target - self.value) * k.TYPING_DECAY_ALPHA
        return self.value
