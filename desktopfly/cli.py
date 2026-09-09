"""Command line entry point.

Port of the argument handling at the bottom of main.swift, plus the control
client that replaces the menu bar for anyone driving the fly from compositor
keybinds.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

COMMANDS = (
    "pause",
    "resume",
    "brain",
    "body",
    "escape",
    "scare",
    "add-fly",
    "remove-fly",
    "next-output",
    "quit",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desktop-fly",
        description="A connectome-driven fruit fly on the Linux desktop.",
        epilog="With no arguments the fly is released onto the current output.",
    )
    parser.add_argument("--config", type=Path, help="path to a config.toml")
    parser.add_argument(
        "--output", help="name of the output to live on, e.g. eDP-1 (overrides the config)"
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="print the detected backends and every sensor reading, then exit",
    )
    parser.add_argument(
        "--simtest", action="store_true", help="circuit invariants, headless, exit 0 on pass"
    )
    parser.add_argument(
        "--behaviortest",
        action="store_true",
        help="end-to-end neuron-to-body checks, headless, exit 0 on pass",
    )
    parser.add_argument(
        "--locomotortest",
        action="store_true",
        help="MaleCNS circuit, leg mechanics and the loop between them, headless",
    )
    parser.add_argument(
        "--snapshot", metavar="PATH", help="offscreen render of the body, for comparison"
    )
    parser.add_argument(
        "--top",
        action="store_true",
        help="render the overlay's own top-down view, which is the only one users see",
    )
    parser.add_argument("--flying", action="store_true", help="snapshot a body in flight")
    parser.add_argument(
        "--walking", action="store_true", help="snapshot a pose driven by live motor neurons"
    )
    parser.add_argument("--beetle", action="store_true", help="snapshot the stag-beetle body")
    parser.add_argument("--brainshot", metavar="PATH", help="offscreen render of the brain map")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed the network's random baselines and noise, for reproducible runs",
    )
    parser.add_argument("--verbose", action="store_true", help="log what the backends decided")

    subparsers = parser.add_subparsers(dest="subcommand")
    control = subparsers.add_parser(
        "ctl", help="send a command to a running fly (the same set as the tray menu)"
    )
    control.add_argument("command", choices=COMMANDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="desktop-fly: %(message)s"
    )

    if args.subcommand == "ctl":
        from .runtime import send_command

        return send_command(args.command)

    if args.simtest:
        from .selftest.sim_test import run

        return run(seed=args.seed)
    if args.behaviortest:
        from .selftest.behavior_test import run as run_behavior

        return run_behavior(seed=args.seed)
    if args.locomotortest:
        from .selftest.locomotor_test import run as run_locomotor

        return run_locomotor(seed=args.seed)
    if args.probe:
        from .platform.detect import detect

        for line in detect().describe():
            print(line)
        return 0
    if args.snapshot:
        from .geometry import BodyForm
        from .render.offscreen import snapshot_fly

        snapshot_fly(
            Path(args.snapshot),
            form=BodyForm.BEETLE if args.beetle else BodyForm.FLY,
            top_down=args.top,
            flying=args.flying,
            walking=args.walking,
        )
        print(f"snapshot written to {args.snapshot}")
        return 0
    if args.brainshot:
        from .render.offscreen import snapshot_brain

        snapshot_brain(Path(args.brainshot))
        print(f"snapshot written to {args.brainshot}")
        return 0

    from .config import load
    from .runtime import Application

    config = load(args.config)
    if args.output:
        config.display.output = args.output
    if args.seed is not None:
        config.sim.seed = args.seed
    return Application(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
