"""Every tuning constant of the simulation and the behaviour model.

Port of the constants scattered through upstream's Sim.swift, main.swift,
FlyModel.swift and Environment.swift. The values are unchanged: they are the
operating point that upstream's two test suites certify, and DESIGN.md section
5.1 records why several of them cannot be touched casually.

User-facing settings (output choice, poll rates, sensor preferences) live in
config.py instead. What is here is physics, not preference.
"""

from typing import Final

# ---------------------------------------------------------------------------
# LIF network — port of Sim.swift
# ---------------------------------------------------------------------------

SIM_STEP_MS: Final = 1  # the network is integrated at 1 kHz

# exp(-1/20): a 20 ms membrane time constant sampled at the 1 ms step above
MEMBRANE_DECAY: Final = 0.9512
SPIKE_THRESHOLD: Final = 1.0
REFRACTORY_MS: Final = 2.0

# Volts per synapse. Every edge weight in data/circuit.json is a signed synapse
# count, so this single scale sets the gain of the whole real graph.
WEIGHT_SCALE: Final = 0.0008

# The membrane cannot be driven arbitrarily far below rest by inhibition.
MEMBRANE_FLOOR: Final = -2.0

# GABA and glutamate synapses deliver a few milliseconds late while the LC->GF
# electrical coupling is instantaneous. This window is the whole reason the
# giant fiber can fire before ~1200 synapses of feedforward inhibition arrive,
# which is why a slow approach is tolerated and a fast lunge is not.
INHIBITORY_DELAY_MS: Final = 4
INHIBITORY_QUEUE_SLOTS: Final = INHIBITORY_DELAY_MS + 1

# Chemical synapse counts under-represent the documented electrical coupling
# from the looming detectors and the wind-sensitive pathway onto the giant
# fiber, so that drive is boosted.
GAP_JUNCTION_BOOST: Final = 6.0

# Per-millisecond probability of a spontaneous depolarising kick, and its size.
NOISE_PROBABILITY: Final = 0.0022
NOISE_KICK: Final = 0.42

# Occasional network-wide arousal bursts: noise is multiplied for a short while,
# then the next burst is scheduled a random interval away.
BURST_NOISE_FACTOR: Final = 6.0
BURST_DURATION_MS: Final = 400
BURST_FIRST_MS: Final = 12_000
BURST_INTERVAL_MS: Final = (15_000, 40_000)

# Exponential moving average for population firing rates: 1/120 per millisecond.
RATE_ALPHA: Final = 1.0 / 120.0

# Input gains onto the three sensory entry points of the circuit.
LOOM_GAIN: Final = 0.30  # looming value -> LC4/LPLC2 membrane
GAIT_GAIN: Final = 0.09  # body gait -> ascending (proprioceptive) partners
AIR_PUFF_GAIN: Final = 0.12  # wind -> sensory partners

# Resting drive per role. Interneurons crackle at a few hertz; the command
# neurons sit quiet until the network drives them.
#
# Bilateral command pairs get a deterministic, side-symmetric baseline on
# purpose: any left/right asymmetry the fly shows must come from the real
# wiring, never from which side happened to draw a larger random number.
BASELINE_OTHER: Final = (0.010, 0.070)  # random per neuron, inclusive range
BASELINE_LOOM: Final = 0.004  # lc4, lplc2
BASELINE_COMMAND: Final = 0.036  # dna01, dna02, mdn, dng11, escw
BASELINE_FORWARD: Final = 0.038  # dnp09
BASELINE_QUIET: Final = 0.002  # gf and anything unclassified

# Brain-window clicks stimulate a cluster of nearby neurons.
STIM_PENDING_LIMIT: Final = 8

# Spike hand-off to the brain window: bounded queue, sampled under heavy load so
# a busy network cannot flood the view.
SPIKE_BUS_CAPACITY: Final = 256
SPIKE_SAMPLE_TARGET: Final = 12

# ---------------------------------------------------------------------------
# Rates to body commands — port of main.swift SignalBuilder
# ---------------------------------------------------------------------------

# The connectome has a standing left/right asymmetry in the steering neurons.
# Adapting it out over ~8 s keeps steady-state walking straight, so only
# transient asymmetries (a threat on one side, a click) actually steer.
DNA_ADAPT_TAU_S: Final = 8.0

NERVOUS_PER_HZ: Final = 1.0 / 80.0  # LC4+LPLC2 population rate -> 0..1
TURN_BIAS_PER_HZ: Final = 0.04  # DNa left-right difference -> rad/s
BACKWARD_HZ: Final = 8.0  # MDN rate above this means a moonwalk burst
WALK_DRIVE_PER_HZ: Final = 1.0 / 10.0  # DNp09
GROOM_DRIVE_PER_HZ: Final = 1.0 / 8.0  # DNg11
WING_DRIVE_PER_HZ: Final = 1.0 / 10.0  # DNp02/04/11
AROUSAL_PER_HZ: Final = 1.0 / 20.0  # whole-population rate

# Every command is clamped. An unclamped walk drive once sent the fly across
# the screen at 1100 pt/s.
TURN_BIAS_LIMIT: Final = 1.0
WALK_DRIVE_LIMIT: Final = 1.3
WING_DRIVE_LIMIT: Final = 1.3

# ---------------------------------------------------------------------------
# Sensory transduction — port of main.swift Coordinator
# ---------------------------------------------------------------------------

# Cursor kinematics -> looming. Everything downstream of these numbers is the
# real connectome; this is the one place where a desktop event becomes a
# stimulus, so it is also the only place tuned by feel.
LOOM_MIN_DISTANCE: Final = 20.0  # px, avoids a singularity at the fly itself
LOOM_APPROACH_GAIN: Final = 6.0
LOOM_FALLOFF_PX: Final = 800.0
LOOM_NEAR_PX: Final = 130.0  # a cursor parked this close is simply a big object
LOOM_NEAR_WEIGHT: Final = 0.5
LOOM_EYE_FLOOR: Final = 0.12  # neither eye ever sees nothing
MOUSE_VELOCITY_ALPHA: Final = 0.4

AIR_PUFF_SPEED_PX_S: Final = 1500.0  # cursor speed that counts as a full gust
AIR_PUFF_FALLOFF_PX: Final = 500.0
TYPING_AIR_PUFF: Final = 0.30  # keystrokes are substrate vibration, not wind

TAP_FALLOFF_PX: Final = 520.0  # click distance beyond which a tap is unfelt
TAP_MIN_STRENGTH: Final = 0.05
TAP_STIM_BASE: Final = 0.15
TAP_STIM_GAIN: Final = 0.35
TAP_STIM_MS: Final = 130

WINDOW_LOOM_FALLOFF_PX: Final = 480.0
WINDOW_LOOM_SCALE: Final = 0.75
WINDOW_LOOM_MIN: Final = 0.08
WINDOW_LOOM_DECAY_PER_S: Final = 4.0  # exponential, a window loom is one event

ESCAPE_TEST_LOOM: Final = 0.6  # the tray "Escape Test" injects a real stimulus
ESCAPE_TEST_DECAY_PER_S: Final = 1.2

# Sleep raises the arousal threshold rather than switching senses off.
SLEEP_ACTIVITY_SCALE: Final = 0.75
SLEEP_SENSORY_GATE: Final = 0.55

# Circadian activity must be compressed toward 1, never multiplied straight in:
# the neurons rest just below threshold, so a raw multiplier silences the whole
# network. Upstream calls the bug this prevents the "siesta coma".
CIRCADIAN_COMPRESSION: Final = 0.35

MAX_SIM_MS_PER_FRAME: Final = 50  # a stalled frame must not fire a burst
MAX_FRAME_DT_S: Final = 0.05

# ---------------------------------------------------------------------------
# Body — port of FlyModel.swift
# ---------------------------------------------------------------------------

FLY_SCALE: Final = 1.15
EDGE_MARGIN: Final = 50.0  # px kept clear of the output edge when picking targets
WALL_MARGIN: Final = 20.0  # px hard clamp on the walking position

# Legacy distance-based fear, used by the extra flies that carry no brain.
SCARE_RADIUS: Final = 110.0
NERVOUS_RADIUS: Final = 240.0

# State changes need both a hysteresis gap and a minimum dwell time, or the
# fly flickers between states every frame.
STATE_DWELL_S: Final = 0.4

NERVOUS_DART_THRESHOLD: Final = 0.40
DART_COOLDOWN_S: Final = 1.2
DART_DURATION_S: Final = (0.4, 0.9)
DART_SPEED: Final = (110.0, 155.0)

GROOM_ON: Final = 0.5
GROOM_OFF: Final = 0.3
GROOM_NERVOUS_CEILING: Final = 0.3  # a nervous fly does not stop to groom
GROOM_OFF_DWELL_S: Final = 0.6

WALK_ON: Final = 0.22
WALK_OFF: Final = 0.08
WALK_OFF_DWELL_S: Final = 0.5

BACKWARD_DURATION_S: Final = 0.5
BACKWARD_SPEED: Final = -22.0

WALK_SPEED_BASE: Final = 14.0  # px/s at zero walk drive
WALK_SPEED_GAIN: Final = 55.0  # px/s per unit of DNp09 drive
WALK_SPEED_LERP: Final = 3.0  # per second

# Spontaneous takeoff, gated on whole-population arousal.
FLIGHT_CHANCE_AROUSED: Final = 0.6  # per second
FLIGHT_CHANCE_CALM: Final = 0.005
FLIGHT_AROUSAL_GATE: Final = 0.5
FLIGHT_EFFORT_BASE: Final = 0.35
FLIGHT_EFFORT_AROUSAL_GAIN: Final = 0.6

FLIGHT_EFFORT_RANGE: Final = (0.25, 1.0)
FLIGHT_EFFORT_CASUAL: Final = (0.4, 0.75)
FLIGHT_EFFORT_LIMIT: Final = 1.3

# Live modifiers must never weaken a takeoff, hence the max() in behavior.py:
# a regression once halved escape altitude by letting the live formula win.
FLIGHT_EFFORT_KEEP: Final = 0.55
FLIGHT_EFFORT_AROUSAL: Final = 0.25
FLIGHT_EFFORT_WING: Final = 0.6

FLIGHT_MIN_DISTANCE_ESCAPE: Final = 350.0
FLIGHT_MIN_DISTANCE_CASUAL: Final = 260.0
FLIGHT_TARGET_ATTEMPTS: Final = 16
FLIGHT_LEDGE_CHANCE: Final = 0.45  # a casual hop often aims at a window edge
FLIGHT_LEDGE_MIN_WIDTH: Final = 90.0
FLIGHT_LEDGE_INSET: Final = 25.0
FLIGHT_LEDGE_MIN_TRIP: Final = 180.0
FLIGHT_DURATION_ESCAPE: Final = (650.0, 0.45, 1.2)  # px/s, min s, max s
FLIGHT_DURATION_CASUAL: Final = (420.0, 0.7, 2.0)
SCARE_COOLDOWN_ESCAPE_S: Final = 2.0
SCARE_COOLDOWN_CASUAL_S: Final = 2.5

ALTITUDE_SCALE_GAIN: Final = 0.8  # higher is nearer the viewer, so bigger
ALTITUDE_Z: Final = 90.0
LANDING_ALTITUDE: Final = 0.035  # touchdown happens through the flare, never a snap
FLIGHT_RISE_FRACTION: Final = 0.25
FLIGHT_FALL_FRACTION: Final = 0.3
FLIGHT_ALTITUDE_LERP: Final = 6.0
FLARE_ALTITUDE_LERP: Final = 9.0
FLIGHT_PITCH_GAIN: Final = 2.5
FLIGHT_PITCH_LIMIT: Final = 0.45
FLARE_PITCH_LIMIT: Final = 0.35

# Tripod gait.
GAIT_AMPLITUDE: Final = (0.20, 0.50)
GAIT_AMPLITUDE_PER_SPEED: Final = 0.0022
GAIT_FREQUENCY: Final = (3.0, 11.0)  # Hz
GAIT_STANCE_FRACTION: Final = 0.6
GAIT_LIFT: Final = 0.55
GAIT_BOB_Z: Final = 0.35

WING_BEAT_BASE_HZ: Final = 14.0
WING_BEAT_EFFORT_HZ: Final = 10.0
WING_RAISE_THRESHOLD: Final = 0.7  # escape-DN rate that raises the wings on foot
WING_RAISE_LERP: Final = 8.0

BREATHE_AWAKE: Final = (3.0, 0.03)  # rad/s, amplitude
BREATHE_ASLEEP: Final = (1.1, 0.05)  # slower and deeper

# Window edges are terrain: the fly latches on, walks the edge, and leaves.
LEDGE_ATTACH_DISTANCE: Final = 20.0
LEDGE_ATTACH_CHANCE: Final = 0.9  # per second while overlapping an edge
LEDGE_LEAVE_CHANCE: Final = 0.05  # per second while attached
LEDGE_SNAP_LERP: Final = 10.0
LEDGE_ALIGN_LERP: Final = 6.0
LEDGE_WANDER: Final = 0.2  # rad/s of heading noise while on an edge
LEDGE_END_MARGIN: Final = 6.0
LEDGE_LOST_DISTANCE: Final = 40.0  # the edge moved this far: the ground vanished

FREE_WANDER: Final = 1.6  # rad/s of heading noise while walking the desktop
BOUNDARY_STEER_LERP: Final = 4.0

# ---------------------------------------------------------------------------
# Desktop senses — port of Environment.swift
# ---------------------------------------------------------------------------

# Drosophila activity over the day: morning and evening peaks, a midday siesta,
# night quiescence. Piecewise linear over (hour, activity).
CIRCADIAN_CURVE: Final = (
    (0.0, 0.25),
    (5.0, 0.25),
    (8.0, 1.0),
    (10.0, 1.0),
    (13.0, 0.55),
    (15.0, 0.55),
    (17.0, 1.0),
    (20.0, 1.0),
    (23.0, 0.3),
    (24.0, 0.25),
)

# Which windows count as terrain at all.
WINDOW_MIN_SIZE: Final = (160.0, 60.0)
LEDGE_MIN_WIDTH: Final = 100.0
LEDGE_SCREEN_MARGIN: Final = 8.0  # an edge flush with the screen edge is unusable
LEDGE_SIDE_MARGIN: Final = 15.0
LEDGE_LIMIT: Final = 12

TYPING_DECAY_ALPHA: Final = 0.15  # smooths keystrokes into a vibration level
TYPING_WINDOW_S: Final = 0.6  # a key pressed this recently still counts

# Flies are ectotherms: a hot machine is a fast fly.
THERMAL_TEMPO_MAX: Final = 1.5
