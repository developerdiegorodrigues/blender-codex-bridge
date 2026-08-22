"""Versioned messages shared by the client and future MCP adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any


PROTOCOL_VERSION = 1
READ_ACTIONS = frozenset(
    {"get_scene_summary", "capture_viewport", "render_workbench_preview", "get_mesh_components"}
)
WRITE_ACTIONS = frozenset(
    {
        "transform_object",
        "undo",
        "save_checkpoint",
        "create_hemisphere",
        "create_image_relief",
        "import_stl_assembly",
        "thicken_mouth_line",
    }
)
ADMIN_ACTIONS = frozenset({"reload_core"})
ALLOWED_ACTIONS = READ_ACTIONS | WRITE_ACTIONS | ADMIN_ACTIONS


class ProtocolError(ValueError):
    """Raised when a command does not conform to the local protocol."""


@dataclass(frozen=True)
class Command:
    request_id: str
    action: str
    arguments: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": self.request_id,
            "action": self.action,
            "arguments": self.arguments,
        }


def _vector3(value: Any, field: str) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
        or any(not isinstance(item, Real) for item in value)
    ):
        raise ProtocolError(f"{field} must contain exactly three numbers")
    return [float(item) for item in value]


def validate_command(payload: Mapping[str, Any]) -> Command:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(f"protocol_version must be {PROTOCOL_VERSION}")

    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ProtocolError("request_id must be a non-empty string of at most 128 characters")

    action = payload.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ProtocolError(f"unsupported action: {action!r}")

    raw_arguments = payload.get("arguments", {})
    if not isinstance(raw_arguments, Mapping):
        raise ProtocolError("arguments must be an object")
    arguments = dict(raw_arguments)

    if action in {"get_scene_summary", "undo", "capture_viewport"}:
        if arguments:
            raise ProtocolError(f"{action} does not accept arguments")
    elif action == "get_mesh_components":
        object_name = arguments.get("object_name")
        if not isinstance(object_name, str) or not object_name or len(object_name) > 255:
            raise ProtocolError("get_mesh_components.object_name must be a non-empty string")
        limit = arguments.get("limit", 12)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ProtocolError("get_mesh_components.limit must be between 1 and 100")
        if set(arguments) - {"object_name", "limit"}:
            raise ProtocolError("get_mesh_components contains unknown arguments")
        arguments = {"object_name": object_name, "limit": limit}
    elif action == "transform_object":
        name = arguments.get("name")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ProtocolError("transform_object.name must be a non-empty string")
        supplied = False
        normalized: dict[str, Any] = {"name": name}
        for field in ("location", "rotation", "scale"):
            if field in arguments:
                normalized[field] = _vector3(arguments[field], field)
                supplied = True
        if not supplied:
            raise ProtocolError("transform_object requires location, rotation, or scale")
        if set(arguments) - {"name", "location", "rotation", "scale"}:
            raise ProtocolError("transform_object contains unknown arguments")
        arguments = normalized
    elif action == "save_checkpoint":
        label = arguments.get("label", "checkpoint")
        if not isinstance(label, str) or not label or len(label) > 80:
            raise ProtocolError("save_checkpoint.label must be a non-empty string")
        if set(arguments) - {"label"}:
            raise ProtocolError("save_checkpoint contains unknown arguments")
        arguments = {"label": label}
    elif action == "create_hemisphere":
        name = arguments.get("name")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ProtocolError("create_hemisphere.name must be a non-empty string")
        location = _vector3(arguments.get("location"), "location")
        radius = arguments.get("radius")
        if not isinstance(radius, Real) or isinstance(radius, bool) or not 0 < radius <= 10:
            raise ProtocolError("create_hemisphere.radius must be between 0 and 10")
        depth = arguments.get("depth", radius)
        if not isinstance(depth, Real) or isinstance(depth, bool) or not 0 < depth <= 10:
            raise ProtocolError("create_hemisphere.depth must be between 0 and 10")
        direction = arguments.get("direction", "-Y")
        if direction not in {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}:
            raise ProtocolError("create_hemisphere.direction is invalid")
        segments = arguments.get("segments", 48)
        rings = arguments.get("rings", 12)
        if not isinstance(segments, int) or isinstance(segments, bool) or not 12 <= segments <= 128:
            raise ProtocolError("create_hemisphere.segments must be between 12 and 128")
        if not isinstance(rings, int) or isinstance(rings, bool) or not 4 <= rings <= 64:
            raise ProtocolError("create_hemisphere.rings must be between 4 and 64")
        if set(arguments) - {"name", "location", "radius", "depth", "direction", "segments", "rings"}:
            raise ProtocolError("create_hemisphere contains unknown arguments")
        arguments = {
            "name": name,
            "location": location,
            "radius": float(radius),
            "depth": float(depth),
            "direction": direction,
            "segments": segments,
            "rings": rings,
        }
    elif action == "create_image_relief":
        name = arguments.get("name")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ProtocolError("create_image_relief.name must be a non-empty string")
        image_path = arguments.get("image_path")
        if not isinstance(image_path, str) or not image_path or len(image_path) > 4096:
            raise ProtocolError("create_image_relief.image_path must be a non-empty string")
        location = _vector3(arguments.get("location"), "location")
        normal = _vector3(arguments.get("normal"), "normal")
        up = _vector3(arguments.get("up"), "up")
        width = arguments.get("width")
        depth = arguments.get("depth")
        if not isinstance(width, Real) or isinstance(width, bool) or not 0 < width <= 10:
            raise ProtocolError("create_image_relief.width must be between 0 and 10")
        if not isinstance(depth, Real) or isinstance(depth, bool) or not 0 < depth <= 1:
            raise ProtocolError("create_image_relief.depth must be between 0 and 1")
        threshold = arguments.get("threshold", 0.5)
        if not isinstance(threshold, Real) or isinstance(threshold, bool) or not 0 < threshold < 1:
            raise ProtocolError("create_image_relief.threshold must be between 0 and 1")
        resolution = arguments.get("resolution", 192)
        if not isinstance(resolution, int) or isinstance(resolution, bool) or not 32 <= resolution <= 512:
            raise ProtocolError("create_image_relief.resolution must be between 32 and 512")
        allowed = {
            "name",
            "image_path",
            "location",
            "normal",
            "up",
            "width",
            "depth",
            "threshold",
            "resolution",
        }
        if set(arguments) - allowed:
            raise ProtocolError("create_image_relief contains unknown arguments")
        arguments = {
            "name": name,
            "image_path": image_path,
            "location": location,
            "normal": normal,
            "up": up,
            "width": float(width),
            "depth": float(depth),
            "threshold": float(threshold),
            "resolution": resolution,
        }
    elif action == "import_stl_assembly":
        name = arguments.get("name")
        if not isinstance(name, str) or not name or len(name) > 120:
            raise ProtocolError("import_stl_assembly.name must be a non-empty string of at most 120 characters")
        parts = arguments.get("parts")
        if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes)) or not 1 <= len(parts) <= 20:
            raise ProtocolError("import_stl_assembly.parts must contain 1 to 20 entries")
        normalized_parts: list[dict[str, str]] = []
        names: set[str] = set()
        for part in parts:
            if not isinstance(part, Mapping) or set(part) != {"name", "path"}:
                raise ProtocolError("each STL part requires name and path")
            part_name, path = part["name"], part["path"]
            if not isinstance(part_name, str) or not part_name or len(part_name) > 255:
                raise ProtocolError("STL part name must be a non-empty string")
            if part_name in names:
                raise ProtocolError("STL part names must be unique")
            if not isinstance(path, str) or not path or len(path) > 4096:
                raise ProtocolError("STL part path must be a non-empty string")
            names.add(part_name)
            normalized_parts.append({"name": part_name, "path": path})
        location = _vector3(arguments.get("location"), "location")
        scale = arguments.get("scale")
        if not isinstance(scale, Real) or isinstance(scale, bool) or not 0.00001 <= scale <= 100:
            raise ProtocolError("import_stl_assembly.scale must be between 0.00001 and 100")
        if set(arguments) - {"name", "parts", "location", "scale"}:
            raise ProtocolError("import_stl_assembly contains unknown arguments")
        arguments = {"name": name, "parts": normalized_parts, "location": location, "scale": float(scale)}
    elif action == "thicken_mouth_line":
        object_name = arguments.get("object_name")
        if not isinstance(object_name, str) or not object_name or len(object_name) > 255:
            raise ProtocolError("thicken_mouth_line.object_name must be a non-empty string")
        points = arguments.get("points")
        if (
            not isinstance(points, Sequence)
            or isinstance(points, (str, bytes))
            or not 2 <= len(points) <= 16
        ):
            raise ProtocolError("thicken_mouth_line.points must contain 2 to 16 points")
        normalized_points = [_vector3(point, "thicken_mouth_line.points") for point in points]
        normal = _vector3(arguments.get("normal"), "normal")
        radius = arguments.get("radius")
        amount = arguments.get("amount")
        surface_window = arguments.get("surface_window", radius)
        if not isinstance(radius, Real) or isinstance(radius, bool) or not 0 < radius <= 1:
            raise ProtocolError("thicken_mouth_line.radius must be between 0 and 1")
        if not isinstance(amount, Real) or isinstance(amount, bool) or not 0 < amount <= 1:
            raise ProtocolError("thicken_mouth_line.amount must be between 0 and 1")
        if (
            not isinstance(surface_window, Real)
            or isinstance(surface_window, bool)
            or not 0 < surface_window <= 1
        ):
            raise ProtocolError("thicken_mouth_line.surface_window must be between 0 and 1")
        normal_threshold = arguments.get("normal_threshold", 0.15)
        if (
            not isinstance(normal_threshold, Real)
            or isinstance(normal_threshold, bool)
            or not -1 <= normal_threshold <= 1
        ):
            raise ProtocolError("thicken_mouth_line.normal_threshold must be between -1 and 1")
        dry_run = arguments.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise ProtocolError("thicken_mouth_line.dry_run must be a boolean")
        allowed = {
            "object_name",
            "points",
            "normal",
            "radius",
            "amount",
            "surface_window",
            "normal_threshold",
            "dry_run",
        }
        if set(arguments) - allowed:
            raise ProtocolError("thicken_mouth_line contains unknown arguments")
        arguments = {
            "object_name": object_name,
            "points": normalized_points,
            "normal": normal,
            "radius": float(radius),
            "amount": float(amount),
            "surface_window": float(surface_window),
            "normal_threshold": float(normal_threshold),
            "dry_run": dry_run,
        }
    elif action == "reload_core":
        release_path = arguments.get("release_path")
        digest = arguments.get("sha256")
        if not isinstance(release_path, str) or not release_path or len(release_path) > 4096:
            raise ProtocolError("reload_core.release_path must be a non-empty string")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ProtocolError("reload_core.sha256 must be a lowercase hexadecimal digest")
        if set(arguments) != {"release_path", "sha256"}:
            raise ProtocolError("reload_core contains unknown arguments")
        arguments = {"release_path": release_path, "sha256": digest}
    elif action == "render_workbench_preview":
        view = arguments.get("view", "front")
        if view not in {"front", "back", "left", "right", "top"}:
            raise ProtocolError("render_workbench_preview.view is invalid")
        target = _vector3(arguments.get("target", [0, 0, 0]), "target")
        ortho_scale = arguments.get("ortho_scale", 1.0)
        resolution = arguments.get("resolution", 800)
        if not isinstance(ortho_scale, Real) or isinstance(ortho_scale, bool) or not 0.01 <= ortho_scale <= 100:
            raise ProtocolError("render_workbench_preview.ortho_scale must be between 0.01 and 100")
        if not isinstance(resolution, int) or isinstance(resolution, bool) or not 128 <= resolution <= 2048:
            raise ProtocolError("render_workbench_preview.resolution must be between 128 and 2048")
        if set(arguments) - {"view", "target", "ortho_scale", "resolution"}:
            raise ProtocolError("render_workbench_preview contains unknown arguments")
        arguments = {
            "view": view,
            "target": target,
            "ortho_scale": float(ortho_scale),
            "resolution": resolution,
        }

    return Command(request_id=request_id, action=action, arguments=arguments)
