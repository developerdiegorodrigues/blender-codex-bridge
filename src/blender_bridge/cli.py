"""Diagnostic and emergency-stop CLI."""

from __future__ import annotations

import argparse
import json
import sys

from .client import BridgeClient
from .runtime import RuntimePaths


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blender-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="show bridge health")
    subparsers.add_parser("scene", help="summarize the open Blender scene")
    checkpoint = subparsers.add_parser("checkpoint", help="save a timestamped copy of the scene")
    checkpoint.add_argument("label", nargs="?", default="checkpoint")
    subparsers.add_parser("capture", help="capture the active 3D viewport")
    hemisphere = subparsers.add_parser("hemisphere", help="create a closed hemisphere")
    hemisphere.add_argument("--name", required=True)
    hemisphere.add_argument("--location", nargs=3, type=float, metavar=("X", "Y", "Z"), required=True)
    hemisphere.add_argument("--radius", type=float, required=True)
    hemisphere.add_argument("--depth", type=float)
    hemisphere.add_argument("--direction", choices=("+X", "-X", "+Y", "-Y", "+Z", "-Z"), default="-Y")
    subparsers.add_parser("stop", help="block queued and future commands")
    subparsers.add_parser("resume", help="remove the emergency stop sentinel")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = RuntimePaths.discover()

    try:
        if args.command == "stop":
            runtime.ensure()
            runtime.stop_file.touch(mode=0o600, exist_ok=True)
            _print({"ok": True, "stopped": True, "path": str(runtime.stop_file)})
        elif args.command == "resume":
            runtime.stop_file.unlink(missing_ok=True)
            _print({"ok": True, "stopped": False})
        elif args.command == "status":
            _print(BridgeClient(runtime).health())
        elif args.command == "scene":
            _print(BridgeClient(runtime).command("get_scene_summary"))
        elif args.command == "checkpoint":
            _print(BridgeClient(runtime).command("save_checkpoint", {"label": args.label}))
        elif args.command == "capture":
            _print(BridgeClient(runtime).command("capture_viewport"))
        elif args.command == "hemisphere":
            arguments = {
                "name": args.name,
                "location": args.location,
                "radius": args.radius,
                "direction": args.direction,
            }
            if args.depth is not None:
                arguments["depth"] = args.depth
            _print(BridgeClient(runtime).command("create_hemisphere", arguments))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
