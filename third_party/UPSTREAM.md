# Vendored upstream code and data

## DesktopFly (macOS)

- Repository: <https://github.com/DenisSergeevitch/desktop-fly>
- Author: Denis Shiryaev
- Licence: MIT (see `../LICENSE`, upstream copyright line retained)
- Vendored commit: `32b00011` — release v1.1.0 (2026-09-05)
- Previous: `7014d37d7e252a3f16b173aca9b49f6f6c91d3b9` (2026-08-18)

### Taken byte-identical, never edited

| path | upstream path | note |
|---|---|---|
| `../etl.py` | `etl.py` | rebuilds the FlyWire files from the raw Codex dumps |
| `../etl_malecns.py` | `etl_malecns.py` | rebuilds the MaleCNS files from the public Feather tables |
| `../data/brain_points.json` | `data/brain_points.json` | 23 210 soma positions |
| `../data/circuit.json` | `data/circuit.json` | 668 neurons, 18 968 signed edges |
| `../data/locomotor_circuit.json` | `data/locomotor_circuit.json` | 1 045 neurons, 17 224 edges, 708 689 contacts |
| `../data/locomotor_report.json` | `data/locomotor_report.json` | extraction coverage and path evidence |
| `../data/DATA_LICENSE.md` | `data/DATA_LICENSE.md` | the CC BY-NC 4.0 / CC BY 4.0 split and the citations |
| `../data/LOCOMOTOR_PROVENANCE.md` | `data/LOCOMOTOR_PROVENANCE.md` | what the MaleCNS extraction measures and what it models |
| `../assets/fly.png` | `assets/fly.png` | upstream render, used for visual comparison |
| `../assets/brain.png` | `assets/brain.png` | upstream render, used for visual comparison |
| `../assets/beetle.png` | `assets/beetle.png` | upstream render, used for visual comparison |

Checked against upstream's published digests:

```
8f76d94034dcf802453e3a0a8ed5342d122e57d37e2bb5ea28da66de0856f5d6  data/locomotor_circuit.json
453f2566bdb7652d9e17e2c8539c1b0f92b84f0db155f6f19738267ed337b358  data/locomotor_report.json
```

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
| `BeetleModel.swift` | `desktopfly/beetle.py` |
| `Locomotor.swift` | `desktopfly/locomotor.py` |
| `LegDynamics.swift` | `desktopfly/legdynamics.py` |
| `LocomotorTests.swift` | `desktopfly/selftest/locomotor_test.py` |
| `BrainView.swift` | `desktopfly/render/brain.py` |
| `Environment.swift` | `desktopfly/environment.py`, `desktopfly/platform/` |

### Deliberate deviations

These are choices, not oversights, and they are listed so an upstream diff stays
mechanically reviewable.

- **`TestRandom` is not ported.** Upstream added an FNV-1a + LCG32 generator so
  its Swift and JavaScript trees produce identical random streams. This fork has
  no JavaScript twin, and already seeds `random` and `numpy.random.default_rng`
  from `--seed` / `selftest.DEFAULT_SEED`. Only the part that matters is adopted:
  each self-test check reseeds from its own name, so one check cannot move the
  ones after it.
- **The `windows/` Electron port is not ported.** It is a second rewrite of the
  same science for a third platform; this fork is the Linux one.
- **No spin/flash fix is needed.** Upstream's `17c4a9e9` fixed spike flashes
  freezing while the pointer held the rotation, because its flash pool were
  children of the rotating node. Here the flashes are a separate additive cloud
  with their own decay, so the bug never existed.

## FlyWire connectome data

`../data/brain_points.json` and `../data/circuit.json` are derived from FlyWire
FAFB v783 and are **CC BY-NC 4.0**, not MIT. See `../data/DATA_LICENSE.md` for
the terms and the required citations.

This data is the work of neither upstream nor this fork. The neurons were
imaged by Zheng et al. (Cell 2018), reconstructed and proofread by the FlyWire
community (Dorkenwald et al., Nature Methods 2022), their synapses detected by
Buhmann et al. (Nature Methods 2021), their neurotransmitters predicted by
Eckstein et al. (Cell 2024), and released with cell-type annotations by
Dorkenwald et al. and Schlegel et al. (Nature 2024). The README section "Where
the neurons come from" states the chain in full; upstream's contribution is the
selection of which 668 of those neurons to simulate and what each one drives.

## MaleCNS connectome data

`../data/locomotor_circuit.json` and `../data/locomotor_report.json` are derived
from the MaleCNS v1.0 public connectome and are **CC BY 4.0** — a different
licence from the FlyWire files beside them. Credit the MaleCNS collaboration:
FlyEM at HHMI Janelia, the University of Cambridge, the MRC Laboratory of
Molecular Biology, and Google Research.

`../data/LOCOMOTOR_PROVENANCE.md` is the authority on where the boundary between
measured anatomy and modelled physiology falls, and nothing in this fork's
documentation may state the boundary more generously than that file does.
