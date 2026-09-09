# What this port was measured against

The numbers below are what this fork produces on the reference machine, beside
the ones upstream published for the same runs. They exist so that a later change
can be checked against something concrete, and so that nobody has to take "the
port is faithful" on trust.

Reference machine: Arch Linux, Hyprland (Wayland), Python 3.14, NumPy, eDP-1 at
1920x1080. Reproduce with `desktop-fly --locomotortest`.

## Vendored data

Both MaleCNS files are upstream's, byte for byte:

```
8f76d94034dcf802453e3a0a8ed5342d122e57d37e2bb5ea28da66de0856f5d6  data/locomotor_circuit.json
453f2566bdb7652d9e17e2c8539c1b0f92b84f0db155f6f19738267ed337b358  data/locomotor_report.json
```

1 045 neurons — 622 premotor, 220 motor, 153 sensory, 34 ascending,
16 descending — 17 224 directed edges and 708 689 contacts, of which 8 112 edges
carry a negative sign and 113 carry no current because their transmitter is
unknown or modulatory. The anatomy is kept, the current is not invented.

## The nerve cord and the mechanics

Bilateral DNp09 at 40 Hz for 8 s, driven at 120 Hz, mechanics at 600 Hz:

| measurement | this fork | upstream |
|---|---|---|
| forward displacement | 10.644 | 10.64 |
| settled path length (after 3 s) | 60.39 | 60.55 |
| contact onsets, RF/LF/RM/LM/RH/LH | 20/22/19/11/16/14 | 20/22/19/11/16/14 |
| motor spikes, closed loop | 12 868 | 12 868 |
| motor spikes, feedback opened | 13 605 | 13 605 |
| sensory spikes | 34 593 | 34 593 |

DNa steering, 30 Hz forward with a 70 Hz perturbation from 3 s, 10 s run —
settled yaw:

| | this fork | upstream |
|---|---|---|
| straight | −1.249 | −1.248 |
| left | −0.663 | −0.661 |
| right | −1.460 | −1.459 |

MDN at 70 Hz for 10 s: settled forward displacement **−12.69** against
upstream's −12.66. The body walks backwards because the motor neurons make it,
not because a sign was flipped somewhere in the body.

Driving the same model from a 60 Hz display instead of 120 Hz gives
**10.643657** either way, with identical contact counts and an identical
12 868 motor spikes. That is the point of the fixed clock.

Rendered toes agree with the physical feedback to **1.23e-06** units — float32
precision on the transform chain. Thermal tempo reaches the mechanics exactly:
**0** rad of knee disagreement against a reference integration at tempo 0.5, 1
and 2.

## Transitions

Per-tick maxima across the five transition fixtures, each run after 360 ticks of
real motor walking:

| | this fork | upstream | limit |
|---|---|---|---|
| toe | 0.997 | 1.031 | 3.0 units |
| joint | 0.119 | 0.120 | 0.35 rad |
| heading | 0.082 | 0.076 | 0.18 rad |
| pitch | 0.061 | 0.061 | 0.08 rad |

Dragging the ledge out from under the fly produces a takeoff with a **0.000**
unit position jump. Before the fix it teleported across the screen.

## Budget

The complete loop — two 120 Hz ticks of the FlyWire network, the nerve cord and
the leg mechanics, plus the body — costs **2.6 ms per tick**, so **5.2 ms of a
16.7 ms frame at 60 Hz**. The nerve cord alone is 0.8 ms per tick and the
mechanics 0.6 ms.

## What none of this shows

Every number here is a property of this model. The MaleCNS graph measures
contacts, not effective physiological weights; the retained input coverage per
motor channel runs between 21% and 45%; the sensory tuning, the muscle
activation, the body mechanics and the rate transfer between two different
specimens are all modelling choices. A movement surviving a lesion check proves
the movement travelled that path in the simulation. It does not prove the
animal does the same thing.
See [`data/LOCOMOTOR_PROVENANCE.md`](data/LOCOMOTOR_PROVENANCE.md).
