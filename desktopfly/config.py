"""User settings, read from a TOML file that does not have to exist.

Defaults reproduce upstream behaviour exactly, so an empty or missing config is
the intended normal case. Settings exist to let someone retune the fly, not to
let the program avoid a decision: a value that will never differ between
machines is a constant in constants.py, not a key here.

Nothing is hardcoded past the XDG specification's own defaults.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from desktopfly.platform.linux_thermal import (
    DEFAULT_SENSOR_PRIORITY,
    FALLBACK_COLD_C,
    FALLBACK_HOT_C,
)

CONFIG_NAME = "config.toml"
APPLICATION_DIR = "desktop-fly"


@dataclass
class FlyConfig:
    count: int = 1  # extra flies use the legacy distance-based behaviour
    scale: float = 1.15
    edge_margin: float = 50.0


@dataclass
class DisplayConfig:
    output: str = ""  # empty means the compositor's current output
    target_fps: int = 60
    shadows: bool = True


@dataclass
class BrainConfig:
    show_on_start: bool = False
    window_size: tuple[int, int] = (480, 400)


@dataclass
class SensesConfig:
    window_poll_hz: float = 1.4  # upstream polls the window list every 0.7 s
    pointer_poll_hz: float = 30.0
    input_devices: str = "auto"  # "auto" reads /dev/input if permitted, "off" never does
    ledges: bool = True


@dataclass
class ThermalConfig:
    sensors: tuple[str, ...] = DEFAULT_SENSOR_PRIORITY
    cold_c: float = FALLBACK_COLD_C  # only used when the chip exposes no crit or max
    hot_c: float = FALLBACK_HOT_C


@dataclass
class CircadianConfig:
    enabled: bool = True
    sleep_idle_night_s: float = 600.0
    sleep_idle_any_s: float = 1800.0


@dataclass
class SimConfig:
    enabled: bool = True  # false means legacy behaviour only, with no connectome
    max_step_ms: int = 50
    seed: int | None = None


@dataclass
class Config:
    fly: FlyConfig = field(default_factory=FlyConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    senses: SensesConfig = field(default_factory=SensesConfig)
    thermal: ThermalConfig = field(default_factory=ThermalConfig)
    circadian: CircadianConfig = field(default_factory=CircadianConfig)
    sim: SimConfig = field(default_factory=SimConfig)


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APPLICATION_DIR / CONFIG_NAME


def _merge(target: Any, values: dict[str, Any]) -> None:
    """Copy known keys onto a dataclass, converting lists to tuples.

    Unknown keys are ignored rather than rejected: a config written for a newer
    version must not stop an older one from starting.
    """
    known = {f.name: f for f in fields(target)}
    for key, value in values.items():
        field_info = known.get(key)
        if field_info is None:
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        elif isinstance(current, tuple) and isinstance(value, list):
            setattr(target, key, tuple(value))
        else:
            setattr(target, key, value)


def load(path: Path | None = None) -> Config:
    config = Config()
    source = path or config_path()
    try:
        raw = tomllib.loads(source.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return config  # no file, or an unreadable one: upstream defaults stand
    _merge(config, raw)
    return config
