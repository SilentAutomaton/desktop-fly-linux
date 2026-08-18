# Vendored upstream code and data

## DesktopFly (macOS)

- Repository: <https://github.com/DenisSergeevitch/desktop-fly>
- Author: Denis Shiryaev
- Licence: MIT (see `../LICENSE`, upstream copyright line retained)
- Vendored commit: `7014d37d7e252a3f16b173aca9b49f6f6c91d3b9` (2026-08-18)

### Taken byte-identical, never edited

| path | upstream path | note |
|---|---|---|
| `../etl.py` | `etl.py` | rebuilds `data/` from the raw FlyWire Codex dumps |
| `../data/brain_points.json` | `data/brain_points.json` | 23 210 soma positions |
| `../data/circuit.json` | `data/circuit.json` | 668 neurons, 18 968 signed edges |
| `../data/DATA_LICENSE.md` | `data/DATA_LICENSE.md` | CC BY-NC 4.0 terms and citations |
| `../assets/fly.png` | `assets/fly.png` | upstream render, used for visual comparison |
| `../assets/brain.png` | `assets/brain.png` | upstream render, used for visual comparison |

If any of these files is ever changed, it stops being a vendored file and this
table must say so.

### Transliterated, not copied

The Swift sources are not vendored. Their logic is rewritten in Python with all
tuning constants unchanged; every ported block carries a provenance comment
naming the upstream file and function, for example `# port of Sim.swift LIFSim.step`.

| upstream file | ported into |
|---|---|
| `Sim.swift` | `desktopfly/sim.py`, `desktopfly/dataset.py` |
| `main.swift` | `desktopfly/signals.py`, `desktopfly/app.py`, `desktopfly/cli.py`, `desktopfly/tray.py`, `desktopfly/selftest/` |
| `FlyModel.swift` | `desktopfly/behavior.py`, `desktopfly/geometry.py` |
| `BrainView.swift` | `desktopfly/render/brain.py` |
| `Environment.swift` | `desktopfly/environment.py`, `desktopfly/platform/` |

## FlyWire connectome data

`../data/*.json` are derived from FlyWire FAFB v783 and are **CC BY-NC 4.0**,
not MIT. See `../data/DATA_LICENSE.md` for the terms and the two required
citations.
