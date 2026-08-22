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
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
