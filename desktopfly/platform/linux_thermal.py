"""CPU temperature from sysfs, normalised without any per-machine tuning.

Replaces ProcessInfo.thermalState. macOS hands out four coarse buckets; Linux
hands out a number, so the number is read and normalised against the same
chip's own critical threshold. That is what makes one code path work on a
100 C Intel laptop and an 85 C ARM board with no configuration.

Nothing here is a hardcoded path: /sys/class/hwmon and /sys/class/thermal are
kernel ABI, and which chip to read is decided by its `name` file.
"""

from __future__ import annotations

import re
from pathlib import Path

from desktopfly.platform.base import ThermalSource

HWMON_ROOT = Path("/sys/class/hwmon")
THERMAL_ROOT = Path("/sys/class/thermal")

# Driver names that report a CPU package temperature, best first. Configurable,
# because a machine can always turn up with a sensor nobody has seen yet.
DEFAULT_SENSOR_PRIORITY = (
    "coretemp",  # Intel
    "k10temp",  # AMD
    "zenpower",  # AMD, out-of-tree driver
    "cpu_thermal",  # ARM SoCs
    "cpu-thermal",
    "soc_thermal",
    "x86_pkg_temp",  # thermal zone name for the Intel package sensor
    "acpitz",  # last resort: the ACPI thermal zone
)

# Labels for the package-wide reading, preferred over a single core.
PACKAGE_LABELS = ("package", "tctl", "tdie", "cpu")

# Used only when the sensor exposes neither a critical nor a maximum threshold.
FALLBACK_COLD_C = 40.0
FALLBACK_HOT_C = 95.0


def _read_number(path: Path) -> float | None:
    try:
        return float(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_name(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


class SysfsThermal(ThermalSource):
    """Picks one sensor at start-up and then only reads that file."""

    def __init__(
        self,
        priority: tuple[str, ...] = DEFAULT_SENSOR_PRIORITY,
        cold_c: float = FALLBACK_COLD_C,
        hot_c: float = FALLBACK_HOT_C,
    ) -> None:
        self.priority = priority
        self.cold_c = cold_c
        self.hot_c = hot_c
        self.sensor_name = ""
        self.input_path: Path | None = None
        self.low_c = cold_c
        self.high_c = hot_c
        self._discover()
        self.available = self.input_path is not None

    def _discover(self) -> None:
        found = self._discover_hwmon() or self._discover_thermal_zone()
        if found is None:
            return
        self.sensor_name, self.input_path, limit = found
        if limit is not None:
            # The chip says where it is unhappy; treat two thirds of the way
            # there as "warm" so an ordinary load already speeds the fly up.
            self.high_c = limit
            self.low_c = min(self.cold_c, limit - 20.0)

    def _discover_hwmon(self) -> tuple[str, Path, float | None] | None:
        chips = {_read_name(path / "name"): path for path in sorted(HWMON_ROOT.glob("hwmon*"))}
        for wanted in self.priority:
            chip = chips.get(wanted)
            if chip is None:
                continue
            inputs = sorted(chip.glob("temp*_input"))
            if not inputs:
                continue
            chosen = self._prefer_package_label(inputs)
            index = re.sub(r"\D", "", chosen.name)
            limit = None
            for suffix in ("crit", "max"):
                value = _read_number(chip / f"temp{index}_{suffix}")
                if value:
                    limit = value / 1000.0
                    break
            return wanted, chosen, limit
        return None

    @staticmethod
    def _prefer_package_label(inputs: list[Path]) -> Path:
        for path in inputs:
            label = _read_name(path.with_name(path.name.replace("_input", "_label"))).lower()
            if any(wanted in label for wanted in PACKAGE_LABELS):
                return path
        return inputs[0]

    def _discover_thermal_zone(self) -> tuple[str, Path, float | None] | None:
        zones = {
            _read_name(zone / "type"): zone for zone in sorted(THERMAL_ROOT.glob("thermal_zone*"))
        }
        for wanted in self.priority:
            zone = zones.get(wanted)
            if zone is not None and (zone / "temp").exists():
                return wanted, zone / "temp", None
        return None

    def celsius(self) -> float | None:
        if self.input_path is None:
            return None
        raw = _read_number(self.input_path)
        if raw is None:
            return None
        return raw / 1000.0  # sysfs reports millidegrees

    def load(self) -> float | None:
        temperature = self.celsius()
        if temperature is None:
            return None
        span = max(1.0, self.high_c - self.low_c)
        return min(1.0, max(0.0, (temperature - self.low_c) / span))

    def describe(self) -> str:
        if self.input_path is None:
            return "thermal: no CPU sensor found — constant tempo"
        temperature = self.celsius()
        reading = f"{temperature:.1f}C" if temperature is not None else "unreadable"
        return (
            f"thermal: {self.sensor_name} {self.input_path.name} = {reading} "
            f"(band {self.low_c:.0f}-{self.high_c:.0f}C, load {self.load()})"
        )
