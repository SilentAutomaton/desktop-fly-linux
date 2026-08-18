"""Command line entry point.

Port of the argument handling at the bottom of main.swift. The run, control
socket, probe and snapshot subcommands are added as their modules land; the two
test suites work from the start because the core is headless.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desktop-fly",
        description="A connectome-driven fruit fly on the Linux desktop.",
    )
    parser.add_argument(
        "--simtest", action="store_true", help="circuit invariants, headless, exit 0 on pass"
    )
    parser.add_argument(
        "--behaviortest",
        action="store_true",
        help="17 end-to-end neuron-to-body checks, headless, exit 0 on pass",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="print the detected backends and every sensor reading, then exit",
    )
    parser.add_argument(
        "--snapshot", metavar="PATH", help="offscreen render of the fly body, for comparison"
    )
    parser.add_argument("--brainshot", metavar="PATH", help="offscreen render of the brain map")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed the network's random baselines and noise, for reproducible runs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.simtest:
        from .selftest.sim_test import run

        return run(seed=args.seed)
    if args.behaviortest:
        from .selftest.behavior_test import run as run_behavior

        return run_behavior(seed=args.seed)

    if args.probe:
        from .platform.detect import detect

        for line in detect().describe():
            print(line)
        return 0
    if args.snapshot:
        from pathlib import Path

        from .render.offscreen import snapshot_fly

        snapshot_fly(Path(args.snapshot))
        print(f"snapshot written to {args.snapshot}")
        return 0
    if args.brainshot:
        from pathlib import Path

        from .render.offscreen import snapshot_brain

        snapshot_brain(Path(args.brainshot))
        print(f"snapshot written to {args.brainshot}")
        return 0

    print("nothing to do yet: the overlay lands in a later step", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
