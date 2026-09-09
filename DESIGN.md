# DesktopFly for Linux — design document

Status: implemented, tracking upstream v1.1.0 (`32b00011`).
Target host: Arch Linux, Hyprland (Wayland). Portable to sway/river/Wayfire, X11, and later
Windows.

---

## 1. What this is, and whose work it builds on

### 1.1 The original

[**DesktopFly**](https://github.com/DenisSergeevitch/desktop-fly) by **Denis Shiryaev**
(`DenisSergeevitch/desktop-fly`, MIT) puts a 3D fruit fly on the macOS desktop. The fly is not
animated by a script. A leaky-integrate-and-fire (LIF) network of **668 real neurons** and
**18 968 real signed synaptic connections**, cut out of the **FlyWire FAFB v783** connectome,
runs at 1 kHz and decides what the body does:

| body behaviour | real neurons that drive it |
|---|---|
| escape takeoff | DNp01 — the Giant Fiber |
| walk / rest, walking speed | DNp09 |
| steering | DNa01 + DNa02, left minus right rate |
| grooming | DNg11 |
| backward "moonwalk" | MDN |
| nervous darting | LC4 + LPLC2 looming detectors |
| wing-beat effort, threat wing-raise | DNp02 / DNp04 / DNp11 |
| spontaneous takeoff | whole-population arousal |

Desktop events are fed in as **sensory input**: the cursor closing in becomes a looming stimulus
on the real LC4/LPLC2 cells, window top edges become walkable ledges, a window appearing nearby
becomes a looming object, mouse clicks become substrate taps on the wind-sensitive pathway,
typing becomes substrate vibration, the machine's thermal state sets the tempo (flies are
ectotherms), and the clock plus user idleness produce a *Drosophila* circadian rhythm with a
midday siesta and night sleep. Escape is a genuine race inside the connectome: ~1 200 synapses
of feedforward inhibition arrive 4 ms late, which is why a slow approach is tolerated and a fast
lunge triggers takeoff in ~4 ms, exactly as in the animal.

Reference layout of the original (2 829 lines of Swift + Python):

| upstream file | contents |
|---|---|
| `main.swift` | overlay scene, CLI modes, `SignalBuilder`, `Coordinator`, `AppDelegate` |
| `FlyModel.swift` | body form switch, procedural fly body + `Fly` behaviour state machine |
| `BeetleModel.swift` | procedural stag beetle — a second skin for the same contract |
| `Sim.swift` | data loading, `BrainSignals`, `SpikeBus`, `LIFSim`, `SimulationClock` |
| `Locomotor.swift` | MaleCNS nerve-cord dynamics, homolog-rate input, motor output |
| `LegDynamics.swift` | modelled joints, foot contact, and the body motion they cause |
| `LocomotorTests.swift` | causal checks on the cord → body → sensory loop |
| `BrainView.swift` | brain window: point clouds, click-to-stimulate, spike flashes |
| `Environment.swift` | `WindowSense`, circadian curve, user idle, thermal tempo |
| `etl.py` | raw FlyWire Codex dumps → `data/brain_points.json` + `data/circuit.json` |
| `etl_malecns.py` | public MaleCNS tables → `data/locomotor_circuit.json` + report |

### 1.2 Why this fork exists

The science half of DesktopFly is portable arithmetic. The I/O half is not: it is SceneKit,
`CGWindowListCopyWindowInfo`, `CGEventSource`, `ProcessInfo.thermalState` and `NSStatusItem` —
Apple frameworks with no Linux counterpart. A Linux user cannot run a single line of it.

This fork keeps the science half as a faithful transliteration and rebuilds the I/O half on
Linux desktop mechanisms. Two extra goals shape the result:

1. **Environment independence.** The fly must behave the same under a tiling compositor and
   under a floating one. It therefore lives on a `wlr-layer-shell` *overlay* surface with an
   empty input region: a layer surface is never placed in a tiling layout and never stacked
   below normal windows, so neither Hyprland's tiling nor a floating WM changes anything.
   Where layer-shell is unavailable the X11 path uses an override-redirect dock window with an
   empty input shape, which is the same idea one protocol down.
2. **Portability outward.** Every operating-system touch point sits behind a small abstract
   class in `desktopfly/platform/`. A Windows port is one new module implementing those
   classes; no core file changes. Section 8 lists exactly what such a module has to provide.

### 1.3 What is taken from upstream, and how

| taken | how | licence |
|---|---|---|
| `etl.py`, `etl_malecns.py` | **byte-identical**, unmodified | MIT (Denis Shiryaev) |
| `data/brain_points.json`, `data/circuit.json`, `data/DATA_LICENSE.md` | **byte-identical**, unmodified | CC BY-NC 4.0 (FlyWire FAFB v783) |
| `data/locomotor_circuit.json`, `data/locomotor_report.json`, `data/LOCOMOTOR_PROVENANCE.md` | **byte-identical**, unmodified | CC BY 4.0 (MaleCNS v1.0) |
| MaleCNS nerve-cord dynamics and the leg mechanics it drives | transliterated, **all constants unchanged** | MIT |
| the stag-beetle body and the form swap | transliterated | MIT |
| brain-window orbit, zoom, fullscreen and the controls hint | transliterated | MIT |
| LIF dynamics, weights, delays, baselines, gap-junction boost | transliterated Swift → Python, **all constants unchanged** | MIT |
| `SignalBuilder` rate→command mapping | transliterated, all clamps unchanged | MIT |
| `Fly` behaviour state machine, gait, flight, ledges, sleep | transliterated | MIT |
| procedural body geometry (dimensions, colours, leg table) | transliterated | MIT |
| brain-window visualisation, click-to-stimulate, role palette | transliterated | MIT |
| circadian curve, `WindowSense` differ, loom transduction | transliterated | MIT |
| `--simtest`, `--behaviortest` and `--locomotortest` suites, **thresholds included** | transliterated | MIT |
| upstream README's "what's modelled vs measured" honesty section | carried over, credited | MIT |

Vendored upstream commit: `32b00011`, release v1.1.0 (2026-09-05).
Recorded in `third_party/UPSTREAM.md`, together with three deliberate deviations: upstream's
`TestRandom` is not ported (it exists to keep its Swift and JavaScript trees producing identical
streams, and this fork has no JavaScript twin), the `windows/` Electron tree is not ported, and
upstream's spin/spike-flash fix has nothing to fix here.

`data/` now holds two sources under two licences. Because the FlyWire files are **CC BY-NC 4.0**,
the distributed bundle as a whole is non-commercial; the MaleCNS files are **CC BY 4.0**; the code
alone stays MIT. `LICENSE` keeps the upstream copyright line and adds the fork's; `README.md`
carries the full third-party list and every required citation.

### 1.4 What is replaced, and why

| upstream | why it cannot be ported |
|---|---|
| SceneKit scene graph and renderer | Apple-only; replaced by a ~80-line scene graph + one GLSL Blinn-Phong shader |
| `NSWindow` overlay | replaced by layer-shell / X11 dock, see §4.1 |
| `CGWindowListCopyWindowInfo` | no Wayland equivalent exists; replaced per compositor, see §4.2 |
| `NSEvent.mouseLocation` | Wayland forbids global pointer queries; replaced per compositor, see §4.3 |
| global `NSEvent` monitor, `CGEventSource` idle | Wayland forbids global input monitoring; replaced by an opt-in `/dev/input` reader plus derived idle, see §4.4 |
| `ProcessInfo.thermalState` | replaced by hwmon/thermal sysfs, see §4.5 |
| `NSStatusItem` | replaced by a StatusNotifierItem tray icon plus a control socket, see §4.7 |
| `NSPanel` brain window | replaced by a normal `xdg-toplevel`, see §4.8 |
| `Coordinator.enqueue` lock/queue | not needed: one GTK main loop, see §3.3 |

Nothing is dropped. Every upstream feature has a Linux mechanism or a documented degradation.

---

## 2. Feature inventory

Legend: **port** = arithmetic moves over unchanged · **replace** = same behaviour, new
mechanism · **degrade** = works where the platform allows, documented fallback otherwise.

### 2.1 Brain and body (all *port*)

- 668-neuron / 18 968-edge LIF circuit at 1 kHz: LC4 (104), LPLC2 (210), DNp01/GF (2),
  DNa01 (2), DNa02 (2), DNp09 (2), DNg11 (6), MDN (4), DNp02/04/11 (6), 330 strongest partners
  including 24 ascending and 16 sensory.
- Signed weights from neurotransmitter prediction (ACh +1, GABA −1, Glu −1, DA/SER/OCT +0.5),
  `weight_scale = 0.0008` per synapse, 20 ms membrane tau, 2 ms refractory, 4 ms delayed
  inhibition ring buffer, ×6 gap-junction boost on LC→GF and sensory→GF.
- Heterogeneous per-role baselines; deterministic side-symmetric baselines on bilateral command
  pairs, so any left/right asymmetry comes from the wiring and not from luck.
- Occasional arousal noise bursts; whole-population rate as arousal.
- `SignalBuilder`: rates → `escape`, `nervous`, `turn_bias` (with 8 s adaptation of the
  connectome's standing L/R asymmetry), `backward`, `walk_drive`, `groom_drive`, `wing_drive`,
  `arousal`. Every field clamped.
- `Fly` state machine: walking / idle / grooming / flying / sleeping, hysteresis plus a 0.4 s
  dwell guard on state changes, cooldowns on one-shot actions.
- A second circuit, also *port*: a 1 045-neuron / 17 224-edge MaleCNS v1.0 nerve-cord subgraph
  integrated at 1 kHz alongside the brain, driving six muscle channels per leg. Descending rates
  cross a modelled homologous population-rate interface, cell type for cell type; there is no
  cross-specimen synapse in either dataset and none is invented.
- Articulated leg mechanics: three damped joints per leg at a fixed 600 Hz substep, a unilateral
  ground constraint, and a fit of the rigid-body displacement that keeps supporting feet still.
  Walking is what falls out of that; there are no gait oscillators.
- Tripod gait with stance/swing split, backward gait, grooming with the front legs, tucked legs
  in flight, visible wing-beat with motion-blur discs, grounded threat wing-raise, altitude →
  scale, touchdown flare (never a snap), slow deep breathing while asleep. The scripted gait is
  now the fallback used when the nerve cord is absent or disabled.
- Body → brain feedback: joint excursion and speed to the chordotonal organs, hip angle to the
  hair plates, foot load to the campaniform sensilla. Without the cord this degrades to gait phase
  and intensity into the ascending neurons. Fast cursor motion drives the sensory (wind) partners
  either way.
- Two body forms behind one contract. `BodyForm` and `build_body()` pick the geometry; nothing in
  the behaviour layer branches on which one is on screen, and the swap keeps every behaviour
  variable.
- Extra flies: only fly #1 carries the brain; the others use the legacy distance-based
  behaviour, exactly as upstream.

### 2.2 Desktop senses

| sense | upstream (macOS) | this fork (Linux) | kind |
|---|---|---|---|
| click-through overlay | borderless `NSWindow`, `ignoresMouseEvents`, `.floating` level | `wlr-layer-shell` overlay layer, all four anchors, exclusive zone −1, keyboard interactivity none, **empty input region**; X11: override-redirect + `_NET_WM_WINDOW_TYPE_DOCK` + `_NET_WM_STATE_ABOVE`/`_STICKY` + empty input shape | replace |
| window ledges and window looms | `CGWindowListCopyWindowInfo` | Hyprland IPC `clients`; sway/i3 IPC `get_tree`; X11 `_NET_CLIENT_LIST` + geometry. All three return plain rectangles into one shared `WindowSense` differ | replace |
| cursor position | `NSEvent.mouseLocation` | Hyprland IPC `cursorpos`; X11 `XQueryPointer`; **sway/river/Wayfire: no protocol exists → blind mode** | degrade |
| clicks as substrate taps | global `NSEvent` monitor | `/dev/input/event*` `EV_KEY` with a `BTN_*` code, when the `input` group grants read | degrade |
| typing as substrate vibration | `CGEventSource` key idle | `/dev/input/event*` `EV_KEY` with a `KEY_*` code — **the event type only, never which key** | degrade |
| user idle | `CGEventSource.secondsSinceLastEventType` | seconds since the newest of: cursor movement, window-list change, `/dev/input` event | replace |
| thermal tempo | `ProcessInfo.thermalState` (4 buckets) | `/sys/class/hwmon/*` CPU sensor normalised against its own `temp*_crit`/`temp*_max`; `/sys/class/thermal/*` fallback; constant when the machine exposes nothing | replace |
| circadian rhythm and sleep | wall clock + idle | identical, wall clock + the idle above | port |
| multi-display hop | `NSScreen.screens` | layer-shell output binding; outputs from Hyprland `monitors`, sway `get_outputs`, or Xinerama | replace |
| control menu | `NSStatusItem` 🪰 | StatusNotifierItem tray icon with the same menu, plus a unix control socket and `desktop-fly ctl` | replace |
| interactive brain window | floating `NSPanel` | normal `xdg-toplevel`, opened from the tray menu | replace |
| offscreen diagnostics renders | `SCNRenderer.snapshot` | EGL/GBM offscreen framebuffer read back to PNG | replace |

### 2.3 Explicit non-goals

- **GNOME on Wayland gets no native overlay.** Mutter does not implement `wlr-layer-shell` and
  has stated it will not. The app detects this and falls back to the X11 path over XWayland,
  which works but sits below native Wayland windows. Documented in the README matrix, not
  worked around.
- **No global cursor position on sway/river/Wayfire.** No Wayland protocol exposes it and those
  compositors have no IPC command for it. The fly runs in *blind mode*: window looms, taps,
  circadian rhythm and the network's own noise still drive it. This is a real behavioural
  difference and the README says so.
- **No screen-content reading, no keylogging, no input grabbing.** The `/dev/input` reader
  records that a key-class or button-class event happened and when, never which key — the same
  "when, never what" guarantee the upstream README makes. It is opt-in and the app is fully
  functional without it.
- **No compositor-specific hacks** beyond the documented public IPC of each compositor.

---

## 3. Architecture

### 3.1 Layers

```
                      ┌──────────────────────────────────────────┐
   OS  ───senses──▶   │ desktopfly/platform/   (only OS code)    │
                      │  Overlay · WindowSource · PointerSource  │
                      │  TapSource · ThermalSource · OutputSource│
                      └───────────────────┬──────────────────────┘
                                          │ plain dataclasses
                      ┌───────────────────▼──────────────────────┐
                      │ environment.py   WindowSense, circadian, │
                      │                  idle aggregation        │
                      └───────────────────┬──────────────────────┘
                                          │ loom_l, loom_r, air_puff, ledges, tempo…
                      ┌───────────────────▼──────────────────────┐
                      │ sim.py           LIFSim @ 1 kHz          │
                      │ locomotor.py     MaleCNS cord @ 1 kHz    │
                      │ signals.py       SignalBuilder           │
                      └───────────────────┬──────────────────────┘
                                          │ BrainSignals + LegMotorCommand x6
                      ┌───────────────────▼──────────────────────┐
                      │ behavior.py      Fly state machine       │
                      │ legdynamics.py   joints, contact, motion │
                      │ geometry.py      procedural body meshes  │
                      │ beetle.py        the second body         │
                      │ scenegraph.py    Node transforms         │
                      └───────────────────┬──────────────────────┘
                                          │ world matrices + meshes
                      ┌───────────────────▼──────────────────────┐
                      │ render/gl.py     Blinn-Phong, ortho cam  │
                      │ render/brain.py  soma cloud, spikes      │
                      └──────────────────────────────────────────┘
```

The arrow back up is real and load-bearing. `Fly.leg_feedback` — joint angles, joint speeds and
foot loads read off the *displayed* skeleton — feeds `LIFSim.leg_feedback` and from there the
cord's own sensory population, closing the body → brain proprioceptive loop through measured
neurons. `Fly.gait_phase` and `Fly.walking_intensity` still feed `LIFSim.gait_drive` as the
fallback when there is no cord.

### 3.2 Module map

```
desktop-fly/
  DESIGN.md              this document
  README.md              user-facing, full third-party licence list
  LICENSE                MIT, upstream copyright + fork copyright
  config.example.toml    every knob, documented, defaults = upstream behaviour
  pyproject.toml
  etl.py                 upstream, byte-identical
  etl_malecns.py         upstream, byte-identical
  data/                  upstream, byte-identical (CC BY-NC 4.0 and CC BY 4.0)
  third_party/UPSTREAM.md
  desktopfly/
    __main__.py          entry point
    cli.py               argument parsing, ctl client, control socket server
    config.py            tomllib load, XDG lookup, merge over defaults
    constants.py         every tuning constant, annotated with unit + upstream origin
    dataset.py           find and load data/*.json          (Sim.swift findDataDir/loadBrainData)
    sim.py               BrainSignals, SpikeBus, LIFSim, SimulationClock  (Sim.swift)
    locomotor.py         LocomotorSim                        (Locomotor.swift)
    legdynamics.py       LegDynamics, SixLegDynamics         (LegDynamics.swift)
    signals.py           SignalBuilder                       (main.swift)
    behavior.py          Fly, BodyForm handling              (FlyModel.swift)
    geometry.py          procedural fly meshes, build_body   (FlyModel.swift buildFlyModel)
    beetle.py            procedural beetle meshes            (BeetleModel.swift)
    scenegraph.py        Node                                (stand-in for SCNNode)
    environment.py       circadian_activity, WindowSense     (Environment.swift)
    app.py               Coordinator, frame loop             (main.swift Coordinator)
    runtime.py           GTK application, control socket     (main.swift AppDelegate)
    tray.py              StatusNotifierItem menu             (main.swift AppDelegate)
    render/
      gl.py              shader, mesh upload, ortho camera, blob shadow
      brain.py           brain map window                    (BrainView.swift)
      offscreen.py       --snapshot / --brainshot
    platform/
      base.py            the whole OS contract, abstract
      detect.py          probe environment, assemble a backend set, log the choice
      gtk_overlay.py     GTK3 + GLArea; layer-shell on Wayland, dock window on X11
      gtk_brain_window.py  the interactive brain map toplevel
      hyprland.py        Hyprland IPC: clients, monitors, cursorpos, event socket
      sway.py            sway/i3 IPC: get_tree, get_outputs
      x11.py             _NET_CLIENT_LIST, XQueryPointer, Xinerama
      linux_thermal.py   hwmon / thermal_zone discovery
      linux_input.py     opt-in /dev/input activity classes
    selftest/
      sim_test.py        port of runSimtest
      behavior_test.py   port of runBehaviorTest
      locomotor_test.py  port of runLocomotorTests
```

### 3.3 Threading and timing

Upstream runs the simulation on SceneKit's render thread and funnels every cross-thread
mutation through `Coordinator.enqueue` (a lock plus a pending-action queue drained each frame),
because macOS timers, the menu and the global click monitor all live on the main thread.

This port has one GTK main loop. The frame tick (`GLArea` render signal, driven by
`Gdk.FrameClock`), the sensor polls (`GLib.timeout_add`), the tray menu callbacks and the
control-socket handler are all main-loop callbacks, so they cannot interleave. **The enqueue
machinery is therefore dropped** — a deliberate simplification, recorded here so nobody
reintroduces it by cargo cult.

**Everything runs on one fixed clock.** A displayed frame is replayed as whole 120 Hz simulation
ticks (`sim.SimulationClock`): the senses are sampled, the two circuits step, the motor commands
come out, the body integrates and the feedback returns, all on the same tick. Advancing only part
of that at a fixed rate while holding the rest per displayed frame is the same class of bug as the
`min(1, k * dt)` filters, and `--locomotortest` asserts a 60 Hz and a 120 Hz drive produce
bit-identical displacement. The leg mechanics subdivides again, to 600 Hz, because the ground
constraint is resolved per substep and a coarse step lets a toe sink visibly before the substrate
pushes back.

Two things genuinely leave the main loop and keep their locks:

- `SpikeBus` — the brain window drains it from its own `GLArea` tick, which is still the same
  main loop, but the bus is kept locked and bounded (256 events) exactly as upstream, because it
  is the natural place to add a worker later.
- `linux_input.py` — a daemon thread blocking on `/dev/input/event*` reads. It only appends
  timestamps to a lock-protected counter that the main loop drains.

`LIFSim.stimulate()` keeps its lock; the tray and the control socket call it.

Frame budget on the reference laptop, measured: **5.5 ms of a 16.7 ms frame at 60 Hz** for the
whole loop — two 120 Hz ticks of the FlyWire network, the nerve cord (0.8 ms/tick) and the leg
mechanics (0.6 ms/tick). The body is one fly of ~40 nodes and the overlay draws a few thousand
triangles. Sim stepping is capped at 50 ms of simulated time per frame, as upstream, so a stalled
frame cannot produce a burst.

Two things make the cord affordable in Python and neither changes a result: the 48 motor-channel
means are one gather and one segmented sum rather than 48 small reductions, and the sensory
transduction is recomputed per new feedback sample rather than per simulated millisecond, which it
cannot change within.

### 3.4 Coordinate systems

One conversion, done once, in `environment.py`:

- **Compositor space** — global layout pixels, origin top-left, y down. What Hyprland, sway and
  X11 all report.
- **Scene space** — origin at the centre of the fly's output, y **up**, one unit per pixel.
  What `Fly`, `Ledge` and the renderer use, identical to upstream's convention.

`scene = (compositor − output_origin) − output_size/2`, with the y component negated. Fractional
scaling is handled by asking the output for its logical size and rendering at the buffer scale
the compositor gives the surface.

---

## 4. Platform layer

`platform/base.py` is the complete list of things this program needs from an operating system.
It is short on purpose.

```python
@dataclass(frozen=True)
class OutputInfo:
    name: str            # "eDP-1"
    x: int; y: int       # position in compositor space
    width: int; height: int   # logical pixels
    scale: float

@dataclass(frozen=True)
class WindowRect:
    key: int             # stable identity across polls (window handle / address hash)
    x: int; y: int; width: int; height: int   # compositor space
    output: str

@dataclass(frozen=True)
class Activity:
    taps: int            # pointer-button presses since the last drain
    keys: int            # key presses since the last drain — count only, never codes
    last_event_monotonic: float

class Overlay(ABC):          # full-output, click-through, always-on-top GL surface
class OutputSource(ABC):     # list of OutputInfo, and which one is "current"
class WindowSource(ABC):     # list[WindowRect]
class PointerSource(ABC):    # (x, y) in compositor space, or None if forbidden
class TapSource(ABC):        # Activity
class ThermalSource(ABC):    # 0..1 normalised heat, or None
```

Every source also exposes `available: bool` and `describe() -> str`, which is what `--probe`
prints. **A missing source degrades, never raises.**

### 4.1 Overlay — `gtk_overlay.py`

GTK 3 window containing a `Gtk.GLArea` with `set_has_alpha(True)` on an RGBA visual.

*Wayland path* (`GtkLayerShell`): `LAYER_OVERLAY`, all four edges anchored so the surface covers
the output, `set_exclusive_zone(-1)` so panels do not reserve space against it,
`KEYBOARD_INTERACTIVITY_NONE`, `set_namespace("desktop-fly")`, and
`gdk_window.input_shape_combine_region(empty)` so every click, scroll and gesture passes
through. A layer surface is outside the tiling layout and above normal windows by protocol, so
this is exactly the "works on tiling and floating alike" requirement.

*X11 path*: `Gdk.WindowTypeHint.DOCK`, `set_keep_above(True)`, `stick()`, `set_accept_focus(False)`,
`set_skip_taskbar_hint(True)`, and the same empty input shape via the X Shape extension.

*Output hop* (upstream "Move to Next Display"): destroy and rebuild the surface bound to the next
`OutputInfo`, then `Coordinator.retarget(size)` — clear stale terrain, resize the orthographic
camera, clamp the flies back inside. Ported from `AppDelegate.move(to:)`.

### 4.2 Windows — `hyprland.py`, `sway.py`, `x11.py`

All three implement `WindowSource.rects()` and nothing else. The ledge extraction, the
new-window diff and the loom injection stay in `environment.WindowSense`, ported from
`Environment.swift`, so adding a compositor is one small file.

- **Hyprland** — a socket write of `j/clients` to `$XDG_RUNTIME_DIR/hypr/$HIS/.socket.sock`
  (falling back to the legacy `/tmp/hypr/$HIS/.socket.sock`), filtered on
  `mapped && !hidden && workspace == output's active workspace`, excluding our own surfaces by
  `class`. Direct socket I/O rather than spawning `hyprctl`, so a 30 Hz cursor poll costs
  nothing. `j/monitors` supplies `OutputSource`.
- **sway / river / Wayfire** — i3 IPC over `$SWAYSOCK`: `GET_TREE` walked for containers with a
  visible `rect`, `GET_OUTPUTS` for outputs. river and Wayfire are covered by the layer-shell
  overlay; their window lists degrade to empty unless they speak the i3 protocol.
- **X11** — `_NET_CLIENT_LIST` plus per-window geometry and `_NET_FRAME_EXTENTS`, skipping
  windows with `_NET_WM_STATE_HIDDEN`, via `python-xlib`. Also the XWayland fallback.

Upstream's filters are kept verbatim: normal windows only, alpha > 0.05, at least 160×60 px,
ledge only if the top edge is inside the screen with 8 px of margin and at least 100 px wide,
maximum 12 ledges.

### 4.3 Pointer — the honest part

| backend | mechanism | result |
|---|---|---|
| Hyprland | IPC `cursorpos` | exact global position |
| X11 / XWayland | `XQueryPointer` on the root window | exact global position |
| sway, river, Wayfire, GNOME-Wayland-native | none exists | `available = False` → blind mode |

Blind mode is not a stub. `computeLoom` simply receives `None`, cursor-driven looming and air
puff go to zero, and the circuit keeps running on window looms, taps, circadian modulation and
its own noise. The fly still walks, grooms, moonwalks, sleeps and takes off spontaneously. This
is stated in the README compatibility matrix rather than hidden.

### 4.4 Taps and vibration — `linux_input.py`

Wayland deliberately gives clients no global input events. The only permission-free signal left
is timing, which is all upstream uses anyway.

When `/dev/input/event*` is readable (the `input` group, which this host already grants), a
daemon thread opens each device that advertises `EV_KEY` and reads 24-byte `input_event`
structs. For each event it increments **one of two counters** — `taps` if the code is in the
`BTN_*` range, `keys` otherwise — and stores the timestamp. Codes are never stored, never
logged, never sent anywhere. That is a strictly narrower capability than upstream's macOS
`CGEventSource` idle query, which also reveals when keys were pressed.

Without the permission, `TapSource.available` is `False` and the app derives its activity signal
from cursor movement and window-list changes. Taps then only reach the sensory pathway through
window events, which is a mild behavioural loss and is documented.

### 4.5 Thermal — `linux_thermal.py`

Flies are ectotherms; upstream maps macOS's four thermal buckets onto a 1.0–1.5 locomotion
tempo. Linux exposes real temperatures, so the port reads one and normalises it, with no
hardcoded model-specific paths:

1. Glob `/sys/class/hwmon/hwmon*/name`. Rank by a priority list of CPU driver names —
   `coretemp`, `k10temp`, `zenpower`, `cpu_thermal`, `cpu-thermal`, `soc_thermal`, `acpitz` —
   configurable in `config.toml`.
2. Inside the chosen chip prefer the input whose `temp*_label` matches `Package id`, `Tctl`,
   `Tdie` or `CPU`; otherwise `temp1_input`.
3. Normalise against that same chip's `temp*_crit`, else `temp*_max`, else the configured
   `[thermal] cold_c` / `hot_c` band. Reading the threshold from the hardware is what makes this
   work on a 100 °C Intel laptop and a 85 °C ARM board without a per-machine config.
4. If no hwmon chip matches, scan `/sys/class/thermal/thermal_zone*/type` for the same names
   (`x86_pkg_temp`, `cpu-thermal`, `acpitz`).
5. If the machine exposes nothing, `available = False` and `tempo` is a constant 1.0.

`tempo = 1.0 + heat * (tempo_max - 1.0)` with `tempo_max = 1.5`, reproducing upstream's range.
Verified against the reference laptop: `hwmon7` is `coretemp`, `temp1_label = "Package id 0"`,
`temp1_crit = 100000` (millidegrees).

### 4.6 Idle and circadian — `environment.py`

`circadian_activity(hour)` is ported unchanged: the *Drosophila* morning and evening peaks, the
midday siesta, night quiescence, piecewise-linear over the same nine control points.

Idle seconds = now − the newest of the last cursor movement, the last window-list change, and
the last `/dev/input` event. Upstream's sleep rule is kept exactly:
`(idle > 600 s and (hour >= 22 or hour < 6)) or idle > 1800 s`.

The neuromodulation formula is kept exactly, including the comment explaining why:
`activity_scale = (1 − (1 − activity) × 0.35) × (0.75 if sleepy else 1)`. The LIF neurons rest
just below threshold, so a raw multiplier silences the whole network — upstream calls the bug
this prevents the "siesta coma".

### 4.7 Control — `tray.py` + `cli.py`

A **tray icon** replaces the menu-bar 🪰 and carries the same menu:

```
Desktop Fly
FlyWire v783 · 23210 somas · circuit 668n/18968e
────────────────────────────
Pause / Resume
Show / Hide Brain          ← opens the interactive brain map
Fullscreen Brain           ← and Hide Brain Hint
Escape Test (loom)
Move to Next Output        ← shown only when more than one output exists
Add Fly · Remove Fly
Scare Flies
Body: Stag Beetle          ← offers the other form, so it reads as an action
────────────────────────────
Quit
```

Implemented over StatusNotifierItem through `AyatanaAppIndicator3` (GObject introspection),
which waybar, KDE, XFCE and GNOME's extensions all speak.

A **unix control socket** at `$XDG_RUNTIME_DIR/desktop-fly.sock` exposes the identical command
set to `desktop-fly ctl <command>`, so the menu can be bound to compositor keys — the natural
way to drive an app on a tiling WM:

```
bind = SUPER SHIFT, F, exec, desktop-fly ctl scare
bind = SUPER SHIFT, B, exec, desktop-fly ctl brain
```

**Tray items and `ctl` commands dispatch through one handler table**, so the two can never
drift apart. If no tray host answers the StatusNotifierItem registration, the app logs a single
line pointing at `ctl` and keeps running.

### 4.8 The brain map window — `render/brain.py`

The interactive window is a required feature of this fork, opened from the tray menu. It is a
normal GTK toplevel (`xdg-toplevel`), *not* a layer surface, because it needs real pointer
input; the compositor decides whether it tiles or floats, and the README ships the Hyprland rule
for people who want it floating:

```
windowrulev2 = float, class:^(desktop-fly-brain)$
windowrulev2 = size 480 400, class:^(desktop-fly-brain)$
```

Contents, ported from `BrainView.swift`:

- **23 210 real soma positions** as a `GL_POINTS` cloud, coloured by FlyWire super-class with
  the upstream palette, additive blending, no depth write, screen-space point radius clamped to
  the upstream 0.7–1.6 px.
- **The 668 circuit neurons** drawn brighter on top, 1.6–2.6 px, in the role palette (looming
  cyan, GF yellow, steering orange, DNp09 green, DNg11 violet, MDN magenta, escape-wing red).
- **The two Giant Fibers** as glowing emissive spheres.
- **Live spikes**: a 48-node flash pool drained from `SpikeBus` each tick, GF flashes bigger and
  slower, exactly as upstream.
- **Direct manipulation**: drag to orbit (pitch clamped short of the poles, so anatomical up stays
  up), scroll to dolly the camera between 9 and 70 units — inside the near and far planes —
  double-click for fullscreen, and a two-line controls hint in the corner, transparent to the mouse
  so it cannot eat a stimulation click, dismissible from the tray. A press that travels under 3 px
  is a click; anything further was an orbit, so aiming and spinning never steal each other's
  gesture.
- **Slow ambient rotation** that pauses while the pointer is inside the window, so a region can be
  aimed at. The flash pool decays regardless, so hovering never stops the activity being aimed at —
  upstream had to fix that, because its flashes were children of the rotating node and ours are
  not.
- Upstream's fullscreen mode carries a pile of AppKit workarounds: a non-activating panel dropped
  one window level below the overlay, a menu-bar inset on the hint, an overridden frame constraint.
  None has a Linux counterpart. A GTK toplevel fullscreens normally and the layer-shell overlay is
  above it by protocol, so the fly still walks across the brain and clicks still reach it.
- **Click to stimulate**: unproject the click into a ray in brain space, find the nearest circuit
  neuron, take every circuit neuron within 2.2 units (clamped to 6…60), inject 0.25 for 400 ms,
  flash an expanding ring and show the region name. What the fly then does is whatever the real
  network does downstream — click the Giant Fiber and it escapes, click DNg11 and it grooms,
  click one side's DNa cluster and it turns that way.

---

## 5. Code standards

These are binding for every file in `desktopfly/`, and are written out here so that a human or
an agent picking the work up has the whole contract in one place.

### 5.1 Language and comments

- All identifiers, comments, commit messages and documentation in **plain technical English**.
- Comments explain **why**, never what. If a line needs a comment to say what it does, rename
  the variable instead.
- Every non-obvious constant, threshold or formula carries the reason it has that value, and
  where it came from. Upstream's hard-won tuning notes are carried over as comments, because
  they encode bugs that already happened once:
  - the operating point is razor-thin — baselines must be compressed toward 1, never scaled
    linearly, or the network goes silent;
  - escape is a race between the boosted LC→GF drive and 4 ms-delayed inhibition, so escape
    tests must use abrupt loom steps, not ramps;
  - live modifiers must never weaken takeoff — flight effort is `max(base, live)`;
  - landing must go through the flare, never a scale or z snap.
- No docstrings on self-evident functions. Module-level docstrings state the module's job and
  the upstream file it came from, in two or three lines.
- Deliberate simplifications are marked `# ponytail:` with the ceiling they accept and the
  upgrade path, e.g. `# ponytail: blob shadow, real shadow map if the overlay ever needs one`.

### 5.2 Naming and traceability

- Upstream names are preserved so the two trees can be read side by side: `LIFSim`,
  `BrainSignals`, `SignalBuilder`, `SpikeBus`, `Fly`, `Ledge`, `WindowSense`, `Coordinator`,
  `circadian_activity`, `compute_loom`, `brain_behavior`.
- Swift `camelCase` becomes `snake_case`; type names stay `PascalCase`; upstream role slugs
  (`lc4`, `lplc2`, `gf`, `dna01`, `dna02`, `dnp09`, `dng11`, `mdn`, `escw`) are untouched
  because they are also the keys in `data/circuit.json`.
- Every ported block starts with a one-line provenance comment:
  `# port of Sim.swift LIFSim.step`. This is what makes an upstream diff mechanically
  reviewable.

### 5.3 Structure

- **Core modules never import platform code, GTK, or OpenGL.** `sim.py`, `signals.py`,
  `behavior.py`, `environment.py`, `constants.py` and `dataset.py` import only the standard
  library and numpy. This is what makes both self-test suites headless, and it is checked by a
  lint rule, not by good intentions.
- One responsibility per module; a module that grows a second one gets split.
- Platform code touches the OS only through `platform/base.py` types. A new compositor or a new
  operating system is a new file plus one line in `detect.py`.
- No abstraction with a single implementation *except* in `platform/`, where the second
  implementation is the entire point.
- Type hints everywhere. `mypy --strict` clean on the core modules.
- `ruff` clean, 100-column lines.

### 5.4 Constants and configuration

- **No magic numbers inline.** Every tuning value lives in `constants.py` with its unit and its
  upstream origin:
  ```python
  WEIGHT_SCALE = 0.0008        # per synapse, Sim.swift:138
  MEMBRANE_DECAY = 0.9512      # exp(-1/20): 20 ms tau at a 1 ms step, Sim.swift:135
  INHIBITORY_DELAY_MS = 4      # GABA/Glut arrive late; this window is why the GF can win
  ```
- **No hardcoded paths.** Config from `$XDG_CONFIG_HOME/desktop-fly/config.toml`, data from
  `$XDG_DATA_HOME/desktop-fly`, the installed package directory, then `/usr/share/desktop-fly`.
  Sysfs found by glob and by the `name` file, never by a fixed `hwmon7`. Compositor sockets
  found from `$HYPRLAND_INSTANCE_SIGNATURE`, `$SWAYSOCK`, `$XDG_RUNTIME_DIR`, `$DISPLAY`.
- Config is read with stdlib `tomllib` and merged over the defaults, so **an empty or missing
  config file reproduces upstream behaviour exactly**. Every key is documented in
  `config.example.toml`.
- Config exists to let a user retune, not to let the program avoid a decision. A value that is
  never going to differ between machines is a constant, not a setting.

### 5.5 Errors

- Sensor and IPC failures degrade to `available = False` with one log line. They never raise
  into the frame loop and never crash the fly.
- Programmer errors are not caught. No bare `except`.
- `--probe` is the single diagnostic entry point: it prints every backend, whether it is
  available, and its current reading.

---

## 6. Configuration schema

`config.example.toml`, fully commented, defaults equal to upstream behaviour:

```toml
[fly]
count = 1                # extra flies use the legacy distance-based behaviour
scale = 1.15             # FlyModel.swift FLY_SCALE
edge_margin = 50         # px kept clear of the output edge

[display]
output = ""              # "" = the compositor's current output; else a name like "eDP-1"
target_fps = 60
shadows = true

[brain]
show_on_start = false    # the tray menu toggles it
window_size = [480, 400]

[senses]
window_poll_hz = 1.4     # upstream polls windows every 0.7 s
pointer_poll_hz = 30
input_devices = "auto"   # "auto" | "off" — /dev/input taps and typing
ledges = true            # window top edges are walkable

[thermal]
sensors = ["coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "soc_thermal", "acpitz"]
cold_c = 40.0            # only used when the sensor exposes no crit/max
hot_c = 95.0
tempo_max = 1.5          # ProcessInfo.thermalState .critical equivalent

[circadian]
enabled = true
sleep_idle_night_s = 600
sleep_idle_any_s = 1800

[sim]
enabled = true           # false = legacy behaviour only, no connectome
max_step_ms = 50         # cap on simulated time per frame
```

---

## 7. Test plan

The two upstream suites are the acceptance criteria and are ported number for number. Upstream
states plainly that they are the ground truth for any change to the simulation or the behaviour,
and this fork adopts that rule.

**`desktop-fly --simtest`** — circuit invariants:

- circuit shape: 668 neurons, LC4 104, LPLC2 210, GF 2, DNa L/R 2/2, DNp09 2, DNg11 6, MDN 4,
  escape-wing 6, ascending 24, sensory 16;
- 4 s of rest: **zero** Giant Fiber spikes;
- abrupt loom (a step, not a ramp): GF fires, first spike within ~10 ms;
- 20 s with walking proprioception: walk-drive duty between 20 % and 50 %;
- siesta at `activity_scale = 0.84`: walk-drive duty still above 3 % — the network must slow
  down, not die;
- 1 s air puff: the wind pathway drives the GF;
- left-eye-only loom: the DNa left−right rate difference moves the right way;
- click-stimulation probes: the GF cluster spikes, the DNg11 cluster raises the groom rate.

**`desktop-fly --behaviortest`** — 23 end-to-end checks, stimulate neurons and assert the body
reacts: GF → flight, DNg11 → grooming, DNp09 → walking with a capped speed, MDN → backward walk
from idle, DNa-left → counter-clockwise turn, moderate loom → dart or escape, sensory tap →
startle escape, ledge attach and follow, window closing underfoot → takeoff, sleep → sleeping
and waking into grooming, thermal tempo scales speed, altitude drives scale and escape flies
higher than a casual hop, wings actually beat, escape-DN activity mid-flight raises effort,
grounded threat raises the wings without taking off, landing has no per-frame scale or z snap,
circadian curve has the right peaks and dips; the body timestep is frame-rate independent; the
elytra of a beetle spread in flight and hold steady rather than buzzing; a threat opens them
without a takeoff; a body swap preserves state, position and the model contract; and the
gait/wing-beat pair re-run under both forms.

**`desktop-fly --locomotortest`** — 18 checks on the nerve cord, the mechanics and the loop
between them. Thirteen are causal, and five of those are lesions, because the only way to show a
movement travelled the path it claims to is to cut the path and watch the movement stop: silence
the motor pool, cut the synapses, open the feedback loop. The remaining five are transition
fixtures, each run after 360 ticks of real motor walking rather than from a reset pose, bounding
how far a joint, a toe, the heading and the pitch may move in one tick across a change of
behaviour. One of them drags the ledge out from under the fly and requires a takeoff with no
position jump.

All three run headless with no display, because the core modules import no GTK and no GL.

Beyond the ported suites:

- `--probe` must report the correct backend set on Hyprland, on sway, and under XWayland.
- `--snapshot` / `--brainshot` produce offscreen renders comparable by eye with the upstream
  `assets/fly.png`, `assets/brain.png` and `assets/beetle.png`. `--top` renders the overlay's own
  orthographic view, which is the only one a user ever sees and therefore the one worth comparing;
  `--walking` drives the pose from live motor neurons rather than a hand-written stride.
- A lint rule asserts that no core module imports `gi`, `OpenGL`, or `desktopfly.platform`.

---

## 8. Porting outward: what a Windows module must provide

Everything in §3.2 outside `platform/` is already portable: pure Python, numpy and OpenGL. A
Windows port is one new `platform/windows.py` implementing the six abstract classes:

| contract | Windows mechanism |
|---|---|
| `Overlay` | layered window: `WS_EX_LAYERED`, `WS_EX_TRANSPARENT`, `WS_EX_TOOLWINDOW`, `WS_EX_NOACTIVATE`, `HWND_TOPMOST`, per-pixel alpha via `UpdateLayeredWindow` or a DWM-composited GL context |
| `OutputSource` | `EnumDisplayMonitors` + `GetMonitorInfo` |
| `WindowSource` | `EnumWindows` filtered by `IsWindowVisible` and `WS_EX_TOOLWINDOW`, geometry from `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` |
| `PointerSource` | `GetCursorPos` |
| `TapSource` | `GetLastInputInfo` for idle; `SetWindowsHookEx(WH_MOUSE_LL)` for taps if the same "when, never what" rule is honoured |
| `ThermalSource` | `MSAcpi_ThermalZoneTemperature` over WMI, or LibreHardwareMonitor when present; `available = False` otherwise, which is a supported state |

The tray is already a cross-platform concept; `tray.py` would gain a second implementation over
the Shell notification area. Nothing in `sim.py`, `signals.py`, `behavior.py`, `geometry.py`,
`environment.py` or `render/` changes. That is the whole point of the split.

---

## 9. Implementation order

1. `DESIGN.md` — this document. *(step 1)*
2. Repo skeleton, vendored `etl.py` and `data/`, `LICENSE`, `third_party/UPSTREAM.md`.
3. Science core: `constants.py`, `dataset.py`, `sim.py`, `signals.py`, `behavior.py`,
   `environment.py`. No I/O, no GTK, no GL.
4. Both self-test suites — **before any pixels**, so the port is proven correct against upstream
   numbers while it is still small.
   *(As built, `scenegraph.py` and `geometry.py` moved into step 3: they are pure geometry with
   no renderer, and `behavior.py` needs the node tree they build.)*
5. Renderer: `scenegraph.py`, `geometry.py`, `render/gl.py`, `render/brain.py`,
   `render/offscreen.py`.
6. Platform layer: `base.py`, `detect.py`, `gtk_overlay.py`, `hyprland.py`, `sway.py`, `x11.py`,
   `linux_thermal.py`, `linux_input.py`.
7. Glue: `app.py`, `cli.py`, `tray.py`, `config.py`.
8. `README.md` with the full third-party licence list and the compatibility matrix, packaging.

Each step is one commit, or a small series, with a message naming the upstream file the code
came from and any deliberate deviation.

### 9.1 Tracking upstream v1.1.0

Upstream's own next release was absorbed in the same shape, one commit per concern:

9. Vendor the MaleCNS data, the second ETL and the split licence.
10. Frame-rate independence (`behavior.lag`), and the cursor-velocity fix that goes with it.
11. Measured walking kinematics: body saccades and a constant-duration swing.
12. The MaleCNS circuit and the leg mechanics; then the wiring and `--locomotortest`.
13. The eased transitions, and the five transition fixtures that guard them.
14. The stag-beetle body and the new snapshot modes.
15. The explorable brain window.
16. Documentation.

The rule that made this reviewable: `--simtest` builds the network *without* the nerve cord, so
its numbers must not move at all. Anything that shifts them is a mistake in the port, not a
feature of the release.

---

## 10. Credits

- **[DesktopFly](https://github.com/DenisSergeevitch/desktop-fly)** by **Denis Shiryaev** — the
  original idea, the connectome circuit selection, the simulation tuning, the behaviour model,
  the body geometry, the ETL and the test suites. MIT.
- **[MaleCNS](https://male-cns.janelia.org/download/)** — the male ventral-nerve-cord connectome
  the legs are driven by. FlyEM at HHMI Janelia with the University of Cambridge, the MRC
  Laboratory of Molecular Biology and Google Research. Data under CC BY 4.0, a different licence
  from the FlyWire files beside it. `data/LOCOMOTOR_PROVENANCE.md` is the authority on what that
  extraction measures and what it models, and no documentation in this fork may claim more than it
  does: the graph measures contacts, not effective weights; the sensory tuning, the muscle
  activation, the body mechanics and the rate transfer between two specimens are modelling
  choices; and a working anatomical path validates the extraction, never the biology.
  The anatomical reading of the coxal rotator channels follows the
  [Azevedo et al. 2024 supplement](https://faculty.washington.edu/tuthill/docs/azevedo24_appendix.pdf).
- **[FlyWire](https://flywire.ai)** / [Codex](https://codex.flywire.ai) — the FAFB v783
  connectome itself. Neither upstream nor this fork digitised any neuron; every number in
  `data/` is downstream of the following measurements, and the README states the chain in full
  under "Where the neurons come from". Data under CC BY-NC 4.0. Cite:
  - Zheng, Z. et al. *A complete electron microscopy volume of the brain of adult Drosophila
    melanogaster.* Cell 174, 730–743 (2018). <https://doi.org/10.1016/j.cell.2018.06.019> —
    the FAFB electron-microscopy volume, imaged in Davi Bock's lab at Janelia.
  - Dorkenwald, S. et al. *FlyWire: online community for whole-brain connectomics.* Nature
    Methods 19, 119–128 (2022). <https://doi.org/10.1038/s41592-021-01330-0> — the segmentation
    and community proofreading platform at Princeton.
  - Buhmann, J. et al. *Automatic detection of synaptic partners in a whole-brain Drosophila
    electron microscopy data set.* Nature Methods 18, 771–774 (2021).
    <https://doi.org/10.1038/s41592-021-01183-7> — the synapse counts on every edge.
  - Eckstein, N. et al. *Neurotransmitter classification from electron microscopy images at
    synaptic sites in Drosophila melanogaster.* Cell 187, 2574–2594 (2024).
    <https://doi.org/10.1016/j.cell.2024.03.016> — the `nt_type` field, which is the only reason
    the weights in this simulation have a sign at all.
  - Dorkenwald, S. et al. *Neuronal wiring diagram of an adult brain.* Nature 634, 124–138
    (2024). <https://doi.org/10.1038/s41586-024-07558-y>
  - Schlegel, P. et al. *Whole-brain annotation and multi-connectome cell typing of Drosophila.*
    Nature 634, 139–152 (2024). <https://doi.org/10.1038/s41586-024-07686-5> — the cell-type
    annotations `etl.py` selects on.
- **The functional literature** the role assignment follows: which cell type drives which
  behaviour is upstream's design choice, informed by published physiology — the giant fiber's
  looming-driven escape (von Reyn et al.; Ache et al.), DNp09 initiating forward walking
  (Bidaye et al., Neuron 2020), MDN driving backward walking (Bidaye et al., Science 2014),
  DNa01/DNa02 steering (Rayshubskiy et al.).

Upstream's honesty section is carried over into the README, and extended for the nerve cord,
because a fly with articulated legs invites more belief than it has earned. The connectome gives
wiring, not physiology. The LIF dynamics, the neurotransmitter signs, the gap-junction boost,
the synaptic delays and the cursor → looming transduction are standard modelling choices layered
on the real graph. Everything downstream of the sensory neurons — who connects to whom, and how
strongly — is FlyWire data.
