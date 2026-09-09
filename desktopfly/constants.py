"""Every tuning constant of the simulation and the behaviour model.

Port of the constants scattered through upstream's Sim.swift, main.swift,
FlyModel.swift and Environment.swift. The values are unchanged: they are the
operating point that upstream's two test suites certify, and DESIGN.md section
5.1 records why several of them cannot be touched casually.

User-facing settings (output choice, poll rates, sensor preferences) live in
config.py instead. What is here is physics, not preference.
"""

import math
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
# MaleCNS locomotor circuit — port of Locomotor.swift
# ---------------------------------------------------------------------------
#
# A second, separate connectome, from a second, separate animal. The anatomy
# and the contact counts are measured; every number in this section is a
# modelling assumption laid on top of them. data/LOCOMOTOR_PROVENANCE.md is the
# authority on where that line falls.

LOCOMOTOR_SYNAPTIC_GAIN: Final = 2.4
LOCOMOTOR_BASELINE: Final = 0.022  # deterministic: this circuit carries no noise
LOCOMOTOR_ADAPTATION_KICK: Final = 0.01  # per spike

# Retained input is normalised per target, so a contact count is never treated
# as a measured conductance. Relative counts and transmitter signs survive; the
# floor stops a sparsely-sampled cell from having enormous effective weights.
LOCOMOTOR_INPUT_FLOOR: Final = 60.0

LOCOMOTOR_MEMBRANE_DECAY: Final = 0.9512294  # exp(-1/20): 20 ms
LOCOMOTOR_MEMBRANE_FLOOR: Final = -1.0
LOCOMOTOR_EXCITATORY_DECAY: Final = 0.8187308  # exp(-1/5): 5 ms synaptic current
LOCOMOTOR_INHIBITORY_DECAY: Final = 0.9048374  # exp(-1/10): 10 ms
LOCOMOTOR_RATE_DECAY: Final = 0.9048374  # 10 ms rate estimate, for fast muscles
LOCOMOTOR_ADAPTATION_DECAY: Final = 0.9950125  # exp(-1/200): 200 ms
LOCOMOTOR_REFRACTORY_MS: Final = 2
LOCOMOTOR_RATE_KICK: Final = 95.16258  # per spike; with the decay above this is Hz

# The homologous population-rate interface between the female FlyWire brain and
# the male nerve cord. It adds current to real cells; it never fabricates a
# graph edge, and there is no cross-specimen synapse in either dataset.
DESCENDING_DRIVE_PER_HZ: Final = 0.004
DESCENDING_DRIVE_LIMIT: Final = 0.35

# Leg-local sensory transduction. The annotation tables do not identify these
# cells' angle or velocity tuning, their preferred direction or the joint they
# sense, so every mapping below is a declared model assumption rather than a
# measured neuron-specific response.
SENSORY_DRIVE_GAIN: Final = 0.10
SENSORY_LOAD_GAIN: Final = 6.0  # campaniform sensilla: load
SENSORY_HAIR_VELOCITY_SCALE: Final = 20.0  # hair plates: hip excursion and speed
SENSORY_KNEE_VELOCITY_SCALE: Final = 20.0  # chordotonal: joint excursion and speed
SENSORY_HIP_VELOCITY_SCALE: Final = 16.0
SENSORY_KNEE_EXCURSION_GAIN: Final = 0.35

# Firing rate at which a muscle channel reaches half activation. A muscle label
# supplies no force, moment arm or activation kinetics, so this saturation is a
# body-model choice too.
MOTOR_HALF_ACTIVATION_HZ: Final = 50.0

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
# The cursor is polled on its own timer while the loom is recomputed every
# frame, so velocity must be measured over the real interval between samples,
# not over the frame. Dividing by the frame time turned one poll into a spike
# whose height scaled with the refresh rate, which made the same gesture reach
# LC4/LPLC2 - and therefore the giant fiber - differently on a 60 Hz and a
# 144 Hz display. 24/60 is the fixed 0.4 the filter used to apply per frame.
MOUSE_VELOCITY_LAG_K: Final = 24.0
MOUSE_RESAMPLE_S: Final = 1.0 / 30.0  # re-measure this often even when quiet,
#                                       so a stopped cursor decays to zero

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

# The whole closed loop - sensing, neurons, motor output, body integration and
# the feedback back into the cord - runs on this fixed tick. Advancing only
# part of it at a fixed rate while holding the rest per displayed frame would
# make the model behave differently on a 60 Hz and a 120 Hz screen.
SIMULATION_TICK_S: Final = 1.0 / 120.0
SIMULATION_MAX_CATCHUP_S: Final = 0.1

# Temperature changes how much mechanical time passes, so force integration,
# foot contact and the sensory feedback that follows all change together.
MOTOR_TEMPO_LIMITS: Final = (0.5, 2.0)

# ---------------------------------------------------------------------------
# Body — port of FlyModel.swift
# ---------------------------------------------------------------------------

# The rate every constant below was tuned at. The body used to advance by
# `min(1, k * dt)` per frame, which is a straight line drawn through a decay
# curve: it matches the tuned value only at 60 Hz and drifts everywhere else.
# behavior.lag() restores the geometric decay those constants already imply,
# and this is the rate at which it must reproduce them exactly.
TUNED_HZ: Final = 60.0

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
LANDING_ALTITUDE: Final = 0.003  # touchdown happens through the flare, never a snap
FLARE_SETTLE_ALTITUDE: Final = 0.2  # the hover wobble fades out below this
PITCH_LERP: Final = 12.0  # the body pitches into and out of a climb, never snaps
FLIGHT_RISE_FRACTION: Final = 0.25
FLIGHT_FALL_FRACTION: Final = 0.3
FLIGHT_ALTITUDE_LERP: Final = 6.0
FLARE_ALTITUDE_LERP: Final = 9.0
FLIGHT_PITCH_GAIN: Final = 2.5
FLIGHT_PITCH_LIMIT: Final = 0.45
FLARE_PITCH_LIMIT: Final = 0.35

# A walking fly does not steer continuously. It goes nearly straight and
# changes heading in discrete body saccades, with slow sub-threshold drift in
# between: Geurten, Jahde, Rosner & Egelhaaf 2014 (Front Behav Neurosci 8:365)
# scored 1140 saccades against 3348 slow turns in freely walking Canton-S at
# 500 fps. The shape of the old code was right and the numbers were not - it
# snapped the heading by up to 86 degrees in a single step.
#
# The measured mean amplitude is ~15 degrees and this range averages to it; the
# sign is drawn separately. A 15 degree turn spent over the measured duration
# peaks near 170 deg/s, just under the 200 deg/s those authors use as their
# saccade detection threshold.
SACCADE_AMPLITUDE: Final = (0.09, 0.44)  # rad, 5 to 25 degrees
SACCADE_DURATION_S: Final = 0.09  # measured 40-120 ms, median 90

# Tripod gait.
GAIT_AMPLITUDE: Final = (0.20, 0.50)
GAIT_AMPLITUDE_PER_SPEED: Final = 0.0022
GAIT_FREQUENCY: Final = (3.0, 11.0)  # Hz
# Swing - the time a leg spends in the air - is near-constant across walking
# speed; it is stance that scales as 1/v. Mendes, Bartos, Akay, Marka & Mann
# 2013 (eLife 2:e00231, Table 2). The gait used a fixed 40% swing fraction,
# which stretched the swing as the fly slowed down, the opposite of the animal.
SWING_DURATION_S: Final = 0.035
GAIT_STANCE_LIMITS: Final = (0.35, 0.9)
GAIT_LIFT: Final = 0.55
GAIT_BOB_Z: Final = 0.35

# Scripted leg poses are blended in from whatever is on screen rather than
# assigned, so a change of behaviour never shows up as a joint snapping back to
# rest. Retargeting from the displayed pose means an interrupted transition
# picks up where it was instead of restarting.
LEG_BLEND_S: Final = 0.18
GAIT_KNEE_ANGLE: Final = 0.75  # the knee an active leg holds; at rest it is REST_KNEE

# The body turns at a rate, not instantly: a proportional heading controller
# with a ceiling on how fast it may turn and on how fast that may change.
TURN_GAIN: Final = 16.0
TURN_RATE_LIMIT: Final = 8.0  # rad/s
TURN_ACCELERATION_LIMIT: Final = 60.0  # rad/s^2

WING_BEAT_BASE_HZ: Final = 22.0
WING_BEAT_EFFORT_HZ: Final = 10.0
# The wings open and close over this lag, and the stroke is gated until they
# have opened: beating through a half-folded wing puts it through the thorax.
WING_FLIGHT_LERP: Final = 18.0
WING_BEAT_GATE: Final = (0.8, 0.2)  # start, width, over the open fraction
WING_STROKE_ROLL: Final = 0.175  # rad of sweep either side of the held spread
WING_RAISE_THRESHOLD: Final = 0.7  # escape-DN rate that raises the wings on foot
WING_RAISE_LERP: Final = 8.0

# The wing cases of a beetle form. Display only: they hold a steady open angle
# rather than buzzing along with the hindwings, the way a real beetle flies.
ELYTRA_LERP: Final = 10.0
ELYTRA_YAW: Final = 0.62  # rad, fully open
ELYTRA_LIFT: Final = 0.85

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

# The heading noise above is a random walk, and a random walk's variance grows
# with dt, not with dt squared. Spending it as `rnd * WANDER * dt` therefore
# made the fly measurably twitchier on a 60 Hz display than on a 120 Hz one:
# 0.168 rad of spread over 2 s against 0.119. Spending it as
# `rnd * JITTER * sqrt(dt)` is frame-rate independent, and dividing by
# sqrt(TUNED_HZ) reproduces the original 60 Hz spread exactly.
WANDER_JITTER: Final = FREE_WANDER / math.sqrt(TUNED_HZ)  # rad/sqrt(s)
LEDGE_JITTER: Final = LEDGE_WANDER / math.sqrt(TUNED_HZ)  # rad/sqrt(s)

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
