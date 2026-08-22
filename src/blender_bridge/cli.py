"""Diagnostic and emergency-stop CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from .client import BridgeClient
from .runtime import RuntimePaths


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _default_core_source() -> Path:
    return Path(__file__).resolve().parents[2] / "addon" / "blender_codex_bridge" / "core.py"


def _deploy(runtime: RuntimePaths, source: Path) -> dict[str, object]:
    source = source.expanduser().resolve()
    if not source.is_file() or source.suffix != ".py":
        raise RuntimeError(f"core source was not found: {source}")
    contents = source.read_bytes()
    if len(contents) > 2 * 1024 * 1024:
        raise RuntimeError("core source exceeds the 2 MiB safety limit")
    compile(contents, str(source), "exec")
    digest = hashlib.sha256(contents).hexdigest()
    runtime.ensure()
    release = runtime.releases_dir / digest[:16]
    release.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = release / "core.py"
    temporary = destination.with_suffix(".tmp")
    shutil.copy2(source, temporary)
    temporary.chmod(0o600)
    temporary.replace(destination)
    result = BridgeClient(runtime).command(
        "reload_core",
        {"release_path": str(destination.resolve()), "sha256": digest},
    )
    if not result.get("ok"):
        raise RuntimeError(f"core deployment was rejected: {result.get('error', 'unknown error')}")
    result["release_path"] = str(destination.resolve())
    return result


def _reload_saved(runtime: RuntimePaths, key: str) -> dict[str, object]:
    state = runtime.core_state()
    release = state.get(key)
    if not isinstance(release, dict):
        raise RuntimeError(f"No {key} core release is available")
    path = release.get("release_path")
    digest = release.get("sha256")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise RuntimeError(f"The {key} core release metadata is invalid")
    result = BridgeClient(runtime).command("reload_core", {"release_path": path, "sha256": digest})
    if not result.get("ok"):
        raise RuntimeError(f"core reload was rejected: {result.get('error', 'unknown error')}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blender-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="show bridge health")
    deploy = subparsers.add_parser("deploy", help="validate, stage, and atomically load a core release")
    deploy.add_argument("--source", type=Path, default=_default_core_source())
    subparsers.add_parser("reload", help="reload the current staged core release")
    subparsers.add_parser("rollback", help="atomically restore the previous core release")
    subparsers.add_parser("scene", help="summarize the open Blender scene")
    checkpoint = subparsers.add_parser("checkpoint", help="save a timestamped copy of the scene")
    checkpoint.add_argument("label", nargs="?", default="checkpoint")
    subparsers.add_parser("capture", help="capture the active 3D viewport")
    preview = subparsers.add_parser("preview", help="render a temporary orthographic workbench view")
    preview.add_argument("--view", choices=("front", "back", "left", "right", "top"), default="front")
    preview.add_argument("--target", nargs=3, type=float, metavar=("X", "Y", "Z"), default=(0, 0, 0))
    preview.add_argument("--ortho-scale", type=float, default=1.0)
    preview.add_argument("--resolution", type=int, default=800)
    hemisphere = subparsers.add_parser("hemisphere", help="create a closed hemisphere")
    hemisphere.add_argument("--name", required=True)
    hemisphere.add_argument("--location", nargs=3, type=float, metavar=("X", "Y", "Z"), required=True)
    hemisphere.add_argument("--radius", type=float, required=True)
    hemisphere.add_argument("--depth", type=float)
    hemisphere.add_argument("--direction", choices=("+X", "-X", "+Y", "-Y", "+Z", "-Z"), default="-Y")
    relief = subparsers.add_parser("relief", help="turn a PNG alpha channel into a closed relief")
    relief.add_argument("--name", required=True)
    relief.add_argument("--image", required=True)
    relief.add_argument("--location", nargs=3, type=float, metavar=("X", "Y", "Z"), required=True)
    relief.add_argument("--normal", nargs=3, type=float, metavar=("X", "Y", "Z"), required=True)
    relief.add_argument("--up", nargs=3, type=float, metavar=("X", "Y", "Z"), required=True)
    relief.add_argument("--width", type=float, required=True)
    relief.add_argument("--depth", type=float, required=True)
    relief.add_argument("--threshold", type=float, default=0.5)
    relief.add_argument("--resolution", type=int, default=192)
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
        elif args.command == "deploy":
            _print(_deploy(runtime, args.source))
        elif args.command == "reload":
            _print(_reload_saved(runtime, "current"))
        elif args.command == "rollback":
            _print(_reload_saved(runtime, "previous"))
        elif args.command == "scene":
            _print(BridgeClient(runtime).command("get_scene_summary"))
        elif args.command == "checkpoint":
            _print(BridgeClient(runtime).command("save_checkpoint", {"label": args.label}))
        elif args.command == "capture":
            _print(BridgeClient(runtime).command("capture_viewport"))
        elif args.command == "preview":
            _print(
                BridgeClient(runtime).command(
                    "render_workbench_preview",
                    {
                        "view": args.view,
                        "target": args.target,
                        "ortho_scale": args.ortho_scale,
                        "resolution": args.resolution,
                    },
                )
            )
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
        elif args.command == "relief":
            _print(
                BridgeClient(runtime).command(
                    "create_image_relief",
                    {
                        "name": args.name,
                        "image_path": args.image,
                        "location": args.location,
                        "normal": args.normal,
                        "up": args.up,
                        "width": args.width,
                        "depth": args.depth,
                        "threshold": args.threshold,
                        "resolution": args.resolution,
                    },
                )
            )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
