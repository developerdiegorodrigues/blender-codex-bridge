"""Blender add-on exposing a small authenticated local control surface."""

from __future__ import annotations

import json
import math
import os
import queue
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import bpy
import bmesh
from mathutils import Matrix, Vector


CORE_API_VERSION = 1
CORE_VERSION = "0.4.6"


def self_test() -> dict[str, object]:
    if bpy.app.version < (4, 0, 0):
        raise RuntimeError("Blender 4.0 or newer is required")
    required = {
        "get_scene_summary",
        "create_hemisphere",
        "create_image_relief",
        "import_stl_assembly",
        "thicken_mouth_line",
    }
    missing = required - ALLOWED_ACTIONS
    if missing:
        raise RuntimeError(f"core is missing required actions: {sorted(missing)}")
    return {"ok": True, "core_version": CORE_VERSION, "actions": sorted(ALLOWED_ACTIONS)}


bl_info = {
    "name": "Blender Codex Bridge",
    "author": "NoctisLab",
    "version": (0, 3, 0),
    "blender": (4, 0, 0),
    "location": "Preferences > Add-ons",
    "description": "Authenticated local bridge for structured Codex tools",
    "category": "Interface",
}

PROTOCOL_VERSION = 1
ALLOWED_ACTIONS = {
    "get_scene_summary",
    "transform_object",
    "undo",
    "save_checkpoint",
    "capture_viewport",
    "create_hemisphere",
    "create_image_relief",
    "import_stl_assembly",
    "render_workbench_preview",
    "get_mesh_components",
    "thicken_mouth_line",
}
MAX_REQUEST_BYTES = 256 * 1024
COMMAND_TIMEOUT_SECONDS = 30.0


def _runtime_dir() -> Path:
    configured = os.environ.get("BLENDER_CODEX_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    return Path(tempfile.gettempdir()) / f"blender-codex-bridge-{uid}"


@dataclass
class PendingCommand:
    payload: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None


@dataclass
class BridgeState:
    runtime: Path
    token: str
    commands: queue.Queue[PendingCommand] = field(default_factory=queue.Queue)
    server: ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        return (self.runtime / "STOP").exists()


STATE: BridgeState | None = None


def _json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: object) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status.value)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "BlenderCodexBridge/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[Blender Codex Bridge] {format % args}")

    def _authorized(self) -> bool:
        assert STATE is not None
        supplied = self.headers.get("Authorization", "")
        return secrets.compare_digest(supplied, f"Bearer {STATE.token}")

    def _require_authorization(self) -> bool:
        if self._authorized():
            return True
        _json_response(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
        return False

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._require_authorization():
            return
        if self.path != "/v1/health":
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        assert STATE is not None
        _json_response(
            self,
            HTTPStatus.OK,
            {
                "ok": True,
                "protocol_version": PROTOCOL_VERSION,
                "blender_version": bpy.app.version_string,
                "stopped": STATE.stopped,
                "queue_depth": STATE.commands.qsize(),
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._require_authorization():
            return
        if self.path != "/v1/commands":
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        assert STATE is not None
        if STATE.stopped:
            _json_response(self, HTTPStatus.LOCKED, {"ok": False, "error": "emergency_stop_active"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_content_length"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            _validate_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        command = PendingCommand(payload=payload)
        STATE.commands.put(command)
        if not command.done.wait(COMMAND_TIMEOUT_SECONDS):
            _json_response(self, HTTPStatus.GATEWAY_TIMEOUT, {"ok": False, "error": "command_timeout"})
            return
        _json_response(self, HTTPStatus.OK, command.response)


def _validate_vector(arguments: dict[str, Any], name: str) -> None:
    if name not in arguments:
        return
    value = arguments[name]
    if not isinstance(value, list) or len(value) != 3 or any(not isinstance(item, (int, float)) for item in value):
        raise ValueError(f"{name} must contain three numbers")


def _validate_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    if payload.get("action") not in ALLOWED_ACTIONS:
        raise ValueError("unsupported action")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ValueError("request_id must be a non-empty string of at most 128 characters")
    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    action = payload["action"]
    if action in {"get_scene_summary", "undo", "capture_viewport"} and arguments:
        raise ValueError(f"{action} does not accept arguments")
    if action == "get_mesh_components":
        object_name = arguments.get("object_name")
        if not isinstance(object_name, str) or not object_name or len(object_name) > 255:
            raise ValueError("get_mesh_components requires a valid object_name")
        limit = arguments.get("limit", 12)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("get_mesh_components.limit must be between 1 and 100")
        if set(arguments) - {"object_name", "limit"}:
            raise ValueError("get_mesh_components contains unknown arguments")
    if action == "transform_object":
        name = arguments.get("name")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ValueError("transform_object requires a name")
        if not any(field in arguments for field in ("location", "rotation", "scale")):
            raise ValueError("transform_object requires location, rotation, or scale")
        if set(arguments) - {"name", "location", "rotation", "scale"}:
            raise ValueError("transform_object contains unknown arguments")
        for field_name in ("location", "rotation", "scale"):
            _validate_vector(arguments, field_name)
    if action == "save_checkpoint":
        label = arguments.get("label", "checkpoint")
        if not isinstance(label, str) or not label or len(label) > 80:
            raise ValueError("save_checkpoint.label must be a non-empty string")
        if set(arguments) - {"label"}:
            raise ValueError("save_checkpoint contains unknown arguments")
    if action == "create_hemisphere":
        name = arguments.get("name")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ValueError("create_hemisphere requires a valid name")
        _validate_vector(arguments, "location")
        if "location" not in arguments:
            raise ValueError("create_hemisphere requires a location")
        radius = arguments.get("radius")
        depth = arguments.get("depth", radius)
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not 0 < radius <= 10:
            raise ValueError("create_hemisphere.radius must be between 0 and 10")
        if isinstance(depth, bool) or not isinstance(depth, (int, float)) or not 0 < depth <= 10:
            raise ValueError("create_hemisphere.depth must be between 0 and 10")
        if arguments.get("direction", "-Y") not in {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}:
            raise ValueError("create_hemisphere.direction is invalid")
        segments = arguments.get("segments", 48)
        rings = arguments.get("rings", 12)
        if isinstance(segments, bool) or not isinstance(segments, int) or not 12 <= segments <= 128:
            raise ValueError("create_hemisphere.segments must be between 12 and 128")
        if isinstance(rings, bool) or not isinstance(rings, int) or not 4 <= rings <= 64:
            raise ValueError("create_hemisphere.rings must be between 4 and 64")
        allowed = {"name", "location", "radius", "depth", "direction", "segments", "rings"}
        if set(arguments) - allowed:
            raise ValueError("create_hemisphere contains unknown arguments")
    if action == "create_image_relief":
        name = arguments.get("name")
        image_path = arguments.get("image_path")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ValueError("create_image_relief requires a valid name")
        if not isinstance(image_path, str) or not image_path or len(image_path) > 4096:
            raise ValueError("create_image_relief requires an image_path")
        for field_name in ("location", "normal", "up"):
            _validate_vector(arguments, field_name)
            if field_name not in arguments:
                raise ValueError(f"create_image_relief requires {field_name}")
        width = arguments.get("width")
        depth = arguments.get("depth")
        threshold = arguments.get("threshold", 0.5)
        resolution = arguments.get("resolution", 192)
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not 0 < width <= 10:
            raise ValueError("create_image_relief.width must be between 0 and 10")
        if isinstance(depth, bool) or not isinstance(depth, (int, float)) or not 0 < depth <= 1:
            raise ValueError("create_image_relief.depth must be between 0 and 1")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold < 1:
            raise ValueError("create_image_relief.threshold must be between 0 and 1")
        if isinstance(resolution, bool) or not isinstance(resolution, int) or not 32 <= resolution <= 512:
            raise ValueError("create_image_relief.resolution must be between 32 and 512")
        allowed = {"name", "image_path", "location", "normal", "up", "width", "depth", "threshold", "resolution"}
        if set(arguments) - allowed:
            raise ValueError("create_image_relief contains unknown arguments")
    if action == "import_stl_assembly":
        name = arguments.get("name")
        if not isinstance(name, str) or not name or len(name) > 120:
            raise ValueError("import_stl_assembly requires a valid name")
        parts = arguments.get("parts")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 20:
            raise ValueError("import_stl_assembly.parts must contain 1 to 20 entries")
        part_names: set[str] = set()
        for part in parts:
            if not isinstance(part, dict) or set(part) != {"name", "path"}:
                raise ValueError("each STL part requires name and path")
            part_name = part.get("name")
            path = part.get("path")
            if not isinstance(part_name, str) or not part_name or len(part_name) > 255:
                raise ValueError("STL part name is invalid")
            if part_name in part_names:
                raise ValueError("STL part names must be unique")
            part_names.add(part_name)
            if not isinstance(path, str) or not path or len(path) > 4096:
                raise ValueError("STL part path is invalid")
        _validate_vector(arguments, "location")
        if "location" not in arguments:
            raise ValueError("import_stl_assembly requires a location")
        scale = arguments.get("scale")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0.00001 <= scale <= 100:
            raise ValueError("import_stl_assembly.scale must be between 0.00001 and 100")
        if set(arguments) - {"name", "parts", "location", "scale"}:
            raise ValueError("import_stl_assembly contains unknown arguments")
    if action == "thicken_mouth_line":
        object_name = arguments.get("object_name")
        if not isinstance(object_name, str) or not object_name or len(object_name) > 255:
            raise ValueError("thicken_mouth_line requires a valid object_name")
        points = arguments.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= 16:
            raise ValueError("thicken_mouth_line.points must contain 2 to 16 points")
        for point in points:
            if not isinstance(point, list) or len(point) != 3 or any(not isinstance(item, (int, float)) for item in point):
                raise ValueError("thicken_mouth_line.points must contain numeric 3D points")
        _validate_vector(arguments, "normal")
        if "normal" not in arguments:
            raise ValueError("thicken_mouth_line requires normal")
        radius = arguments.get("radius")
        amount = arguments.get("amount")
        surface_window = arguments.get("surface_window", radius)
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not 0 < radius <= 1:
            raise ValueError("thicken_mouth_line.radius must be between 0 and 1")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 < amount <= 1:
            raise ValueError("thicken_mouth_line.amount must be between 0 and 1")
        if isinstance(surface_window, bool) or not isinstance(surface_window, (int, float)) or not 0 < surface_window <= 1:
            raise ValueError("thicken_mouth_line.surface_window must be between 0 and 1")
        normal_threshold = arguments.get("normal_threshold", 0.15)
        if (
            isinstance(normal_threshold, bool)
            or not isinstance(normal_threshold, (int, float))
            or not -1 <= normal_threshold <= 1
        ):
            raise ValueError("thicken_mouth_line.normal_threshold must be between -1 and 1")
        dry_run = arguments.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise ValueError("thicken_mouth_line.dry_run must be a boolean")
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
            raise ValueError("thicken_mouth_line contains unknown arguments")
    if action == "render_workbench_preview":
        view = arguments.get("view", "front")
        if view not in {"front", "back", "left", "right", "top"}:
            raise ValueError("render_workbench_preview.view is invalid")
        if "target" in arguments:
            _validate_vector(arguments, "target")
        ortho_scale = arguments.get("ortho_scale", 1.0)
        resolution = arguments.get("resolution", 800)
        if isinstance(ortho_scale, bool) or not isinstance(ortho_scale, (int, float)) or not 0.01 <= ortho_scale <= 100:
            raise ValueError("render_workbench_preview.ortho_scale must be between 0.01 and 100")
        if isinstance(resolution, bool) or not isinstance(resolution, int) or not 128 <= resolution <= 2048:
            raise ValueError("render_workbench_preview.resolution must be between 128 and 2048")
        if set(arguments) - {"view", "target", "ortho_scale", "resolution"}:
            raise ValueError("render_workbench_preview contains unknown arguments")


def _scene_summary() -> dict[str, Any]:
    scene = bpy.context.scene
    return {
        "scene": scene.name,
        "frame": scene.frame_current,
        "active_object": bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        "objects": [
            {
                "name": obj.name,
                "type": obj.type,
                "location": list(obj.location),
                "rotation": list(obj.rotation_euler),
                "scale": list(obj.scale),
                "selected": obj.select_get(),
            }
            for obj in scene.objects
        ],
    }


def _mesh_components(arguments: dict[str, Any]) -> dict[str, Any]:
    obj = bpy.data.objects.get(arguments["object_name"])
    if obj is None or obj.type != "MESH":
        raise ValueError(f"mesh object not found: {arguments['object_name']}")
    mesh = obj.data
    adjacency: list[set[int]] = [set() for _ in mesh.vertices]
    for edge in mesh.edges:
        first, second = edge.vertices
        adjacency[first].add(second)
        adjacency[second].add(first)

    seen: set[int] = set()
    components: list[dict[str, Any]] = []
    for start in range(len(mesh.vertices)):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        vertices: list[int] = []
        while stack:
            index = stack.pop()
            vertices.append(index)
            for neighbor in adjacency[index]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        bounds_min = Vector((math.inf, math.inf, math.inf))
        bounds_max = Vector((-math.inf, -math.inf, -math.inf))
        for index in vertices:
            world = obj.matrix_world @ mesh.vertices[index].co
            bounds_min.x = min(bounds_min.x, world.x)
            bounds_min.y = min(bounds_min.y, world.y)
            bounds_min.z = min(bounds_min.z, world.z)
            bounds_max.x = max(bounds_max.x, world.x)
            bounds_max.y = max(bounds_max.y, world.y)
            bounds_max.z = max(bounds_max.z, world.z)
        components.append(
            {
                "vertices": len(vertices),
                "bounds_min": list(bounds_min),
                "bounds_max": list(bounds_max),
                "center": list((bounds_min + bounds_max) * 0.5),
                "size": list(bounds_max - bounds_min),
            }
        )

    components.sort(key=lambda item: item["vertices"], reverse=True)
    limit = int(arguments.get("limit", 12))
    return {
        "object": obj.name,
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "component_count": len(components),
        "components": components[:limit],
    }


def _transform(arguments: dict[str, Any]) -> dict[str, Any]:
    obj = bpy.data.objects.get(arguments["name"])
    if obj is None:
        raise ValueError(f"object not found: {arguments['name']}")
    if "location" in arguments:
        obj.location = arguments["location"]
    if "rotation" in arguments:
        obj.rotation_euler = arguments["rotation"]
    if "scale" in arguments:
        obj.scale = arguments["scale"]
    bpy.context.view_layer.update()
    return {"object": obj.name, "location": list(obj.location), "rotation": list(obj.rotation_euler), "scale": list(obj.scale)}


def _safe_label(value: object) -> str:
    label = str(value or "checkpoint")[:80]
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-.") or "checkpoint"


def _save_checkpoint(arguments: dict[str, Any]) -> dict[str, Any]:
    assert STATE is not None
    directory = STATE.runtime / "checkpoints"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}-{_safe_label(arguments.get('label'))}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    return {"path": str(path)}


def _capture_viewport(request_id: str) -> dict[str, Any]:
    assert STATE is not None
    directory = STATE.runtime / "captures"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{_safe_label(request_id)}.png"
    scene = bpy.context.scene
    previous_path = scene.render.filepath
    try:
        scene.render.filepath = str(path)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((item for item in area.regions if item.type == "WINDOW"), None)
                if region is None:
                    continue
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.render.opengl(write_still=True, view_context=True)
                return {"path": str(path), "kind": "viewport"}
        raise RuntimeError("no VIEW_3D area is available")
    finally:
        scene.render.filepath = previous_path


def _create_hemisphere(arguments: dict[str, Any]) -> dict[str, Any]:
    name = arguments["name"]
    if bpy.data.objects.get(name) is not None:
        raise ValueError(f"object already exists: {name}")

    radius = float(arguments["radius"])
    depth = float(arguments.get("depth", radius))
    segments = int(arguments.get("segments", 48))
    rings = int(arguments.get("rings", 12))
    direction_name = arguments.get("direction", "-Y")
    direction = {
        "+X": (1.0, 0.0, 0.0),
        "-X": (-1.0, 0.0, 0.0),
        "+Y": (0.0, 1.0, 0.0),
        "-Y": (0.0, -1.0, 0.0),
        "+Z": (0.0, 0.0, 1.0),
        "-Z": (0.0, 0.0, -1.0),
    }[direction_name]
    if direction_name in {"+X", "-X"}:
        tangent_u, tangent_v = (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    elif direction_name in {"+Y", "-Y"}:
        tangent_u, tangent_v = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    else:
        tangent_u, tangent_v = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)

    vertices: list[tuple[float, float, float]] = [
        tuple(component * depth for component in direction)
    ]
    for ring in range(1, rings + 1):
        theta = (math.pi * 0.5) * ring / rings
        radial = radius * math.sin(theta)
        outward = depth * math.cos(theta)
        for segment in range(segments):
            phi = math.tau * segment / segments
            vertices.append(
                tuple(
                    tangent_u[axis] * radial * math.cos(phi)
                    + tangent_v[axis] * radial * math.sin(phi)
                    + direction[axis] * outward
                    for axis in range(3)
                )
            )

    faces: list[tuple[int, ...]] = []
    first_ring = 1
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        faces.append((0, first_ring + segment, first_ring + next_segment))
    for ring in range(rings - 1):
        current = 1 + ring * segments
        following = current + segments
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            faces.append(
                (
                    current + segment,
                    following + segment,
                    following + next_segment,
                    current + next_segment,
                )
            )
    cap_center = len(vertices)
    vertices.append((0.0, 0.0, 0.0))
    equator = 1 + (rings - 1) * segments
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        faces.append((cap_center, equator + next_segment, equator + segment))

    mesh = bpy.data.meshes.new(f"{name}.Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.validate(verbose=False)
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = arguments["location"]
    obj["blender_codex_role"] = "pupil"
    obj["blender_codex_shape"] = "hemisphere"
    bpy.context.collection.objects.link(obj)

    curved_face_count = segments + (rings - 1) * segments
    for polygon in mesh.polygons[:curved_face_count]:
        polygon.use_smooth = True

    material = bpy.data.materials.get("Pupil") or bpy.data.materials.new("Pupil")
    material.diffuse_color = (0.01, 0.01, 0.01, 1.0)
    obj.data.materials.append(material)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()
    return {
        "name": obj.name,
        "location": list(obj.location),
        "radius": radius,
        "depth": depth,
        "direction": direction_name,
        "vertices": len(mesh.vertices),
        "faces": len(mesh.polygons),
    }


def _remove_collinear(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if len(points) < 4:
        return points
    simplified: list[tuple[int, int]] = []
    for index, point in enumerate(points):
        previous = points[index - 1]
        following = points[(index + 1) % len(points)]
        first = (point[0] - previous[0], point[1] - previous[1])
        second = (following[0] - point[0], following[1] - point[1])
        if first[0] * second[1] != first[1] * second[0]:
            simplified.append(point)
    return simplified


def _trace_mask_contours(
    mask: list[list[bool]],
) -> list[list[tuple[int, int]]]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add_edge(start: tuple[int, int], end: tuple[int, int]) -> None:
        edges.setdefault(start, []).append(end)

    for y in range(height):
        for x in range(width):
            if not mask[y][x]:
                continue
            if y == 0 or not mask[y - 1][x]:
                add_edge((x, y), (x + 1, y))
            if x == width - 1 or not mask[y][x + 1]:
                add_edge((x + 1, y), (x + 1, y + 1))
            if y == height - 1 or not mask[y + 1][x]:
                add_edge((x + 1, y + 1), (x, y + 1))
            if x == 0 or not mask[y][x - 1]:
                add_edge((x, y + 1), (x, y))

    contours: list[list[tuple[int, int]]] = []
    while edges:
        start = next(iter(edges))
        current = start
        contour: list[tuple[int, int]] = []
        guard = sum(len(values) for values in edges.values()) + 1
        for _ in range(guard):
            contour.append(current)
            candidates = edges.get(current)
            if not candidates:
                raise RuntimeError("alpha contour is open or self-intersecting")
            following = candidates.pop()
            if not candidates:
                del edges[current]
            current = following
            if current == start:
                break
        else:
            raise RuntimeError("alpha contour tracing exceeded its safety limit")
        contour = _remove_collinear(contour)
        if len(contour) >= 3:
            contours.append(contour)
    return contours


def _alpha_mask(image: bpy.types.Image, resolution: int, threshold: float) -> list[list[bool]]:
    source_width, source_height = image.size
    if source_width <= 0 or source_height <= 0:
        raise ValueError("image has no pixels")
    step = max(1, math.ceil(max(source_width, source_height) / resolution))
    width = math.ceil(source_width / step)
    height = math.ceil(source_height / step)
    pixels = image.pixels[:]
    mask: list[list[bool]] = []
    for y in range(height):
        source_y = min(source_height - 1, y * step + step // 2)
        row: list[bool] = []
        for x in range(width):
            source_x = min(source_width - 1, x * step + step // 2)
            alpha = pixels[(source_y * source_width + source_x) * 4 + 3]
            row.append(alpha >= threshold)
        mask.append(row)
    return mask


def _create_image_relief(arguments: dict[str, Any]) -> dict[str, Any]:
    name = arguments["name"]
    if bpy.data.objects.get(name) is not None:
        raise ValueError(f"object already exists: {name}")
    path = Path(arguments["image_path"]).expanduser().resolve()
    if path.suffix.lower() != ".png" or not path.is_file():
        raise ValueError("image_path must reference an existing PNG file")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("PNG file exceeds the 64 MiB safety limit")

    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        mask = _alpha_mask(
            image,
            int(arguments.get("resolution", 192)),
            float(arguments.get("threshold", 0.5)),
        )
    finally:
        bpy.data.images.remove(image)
    contours = _trace_mask_contours(mask)
    if not contours:
        raise ValueError("PNG alpha channel produced no contours")
    minimum_x = min(point[0] for contour in contours for point in contour)
    maximum_x = max(point[0] for contour in contours for point in contour)
    minimum_y = min(point[1] for contour in contours for point in contour)
    maximum_y = max(point[1] for contour in contours for point in contour)
    pixel_width = maximum_x - minimum_x
    if pixel_width <= 0:
        raise ValueError("PNG alpha contour has zero width")
    scale = float(arguments["width"]) / pixel_width
    center_x = (minimum_x + maximum_x) * 0.5
    center_y = (minimum_y + maximum_y) * 0.5

    curve = bpy.data.curves.new(f"{name}.Curve", type="CURVE")
    curve.dimensions = "2D"
    curve.resolution_u = 1
    curve.render_resolution_u = 1
    curve.fill_mode = "BOTH"
    curve.resolution_v = 0
    curve.extrude = float(arguments["depth"]) * 0.5
    curve.resolution_v = 0
    for contour in contours:
        spline = curve.splines.new("POLY")
        spline.points.add(len(contour) - 1)
        for spline_point, (x, y) in zip(spline.points, contour):
            spline_point.co = ((x - center_x) * scale, (y - center_y) * scale, 0.0, 1.0)
        spline.use_cyclic_u = True

    curve_object = bpy.data.objects.new(f"{name}.Source", curve)
    bpy.context.collection.objects.link(curve_object)
    normal = Vector(arguments["normal"]).normalized()
    up = Vector(arguments["up"])
    up = (up - normal * up.dot(normal)).normalized()
    if up.length < 0.5:
        raise ValueError("normal and up vectors must not be parallel")
    right = up.cross(normal).normalized()
    orientation = Matrix((right, up, normal)).transposed().to_4x4()
    orientation.translation = Vector(arguments["location"])
    curve_object.matrix_world = orientation

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = curve_object.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
    welding = bmesh.new()
    welding.from_mesh(mesh)
    bmesh.ops.remove_doubles(welding, verts=welding.verts[:], dist=1e-7)
    bmesh.ops.recalc_face_normals(welding, faces=welding.faces[:])
    welding.to_mesh(mesh)
    welding.free()
    mesh.validate(verbose=False)
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(name, mesh)
    obj.matrix_world = curve_object.matrix_world.copy()
    bpy.context.collection.objects.link(obj)
    bpy.data.objects.remove(curve_object, do_unlink=True)
    obj["blender_codex_role"] = "image_relief"
    obj["blender_codex_source"] = str(path)

    material = bpy.data.materials.get("Symbol") or bpy.data.materials.new("Symbol")
    material.diffuse_color = (0.03, 0.03, 0.03, 1.0)
    obj.data.materials.append(material)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()
    return {
        "name": obj.name,
        "source": str(path),
        "location": list(obj.location),
        "width": float(arguments["width"]),
        "depth": float(arguments["depth"]),
        "contours": len(contours),
        "vertices": len(mesh.vertices),
        "faces": len(mesh.polygons),
    }


def _import_stl_assembly(arguments: dict[str, Any]) -> dict[str, Any]:
    """Import STL parts that share CAD coordinates as one positioned assembly."""
    name = arguments["name"]
    collection_name = f"{name}.Collection"
    if bpy.data.collections.get(collection_name) is not None:
        raise ValueError(f"assembly collection already exists: {collection_name}")
    for part in arguments["parts"]:
        if bpy.data.objects.get(part["name"]) is not None:
            raise ValueError(f"object already exists: {part['name']}")

    paths: list[tuple[str, Path]] = []
    for part in arguments["parts"]:
        path = Path(part["path"]).expanduser().resolve()
        if path.suffix.lower() != ".stl" or not path.is_file():
            raise ValueError(f"STL file was not found: {path}")
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError(f"STL file exceeds the 64 MiB safety limit: {path.name}")
        paths.append((part["name"], path))

    collection = bpy.data.collections.new(collection_name)
    bpy.context.scene.collection.children.link(collection)
    imported: list[bpy.types.Object] = []
    try:
        for part_name, path in paths:
            before = {obj.name for obj in bpy.data.objects}
            bpy.ops.wm.stl_import(filepath=str(path))
            new_objects = [obj for obj in bpy.data.objects if obj.name not in before]
            meshes = [obj for obj in new_objects if obj.type == "MESH"]
            if len(meshes) != 1:
                raise RuntimeError(f"STL import did not create exactly one mesh: {path.name}")
            obj = meshes[0]
            for old_collection in tuple(obj.users_collection):
                old_collection.objects.unlink(obj)
            collection.objects.link(obj)
            obj.name = part_name
            obj.location = arguments["location"]
            obj.rotation_euler = (0.0, 0.0, 0.0)
            obj.scale = (float(arguments["scale"]),) * 3
            obj["blender_codex_role"] = "stl_assembly_part"
            obj["blender_codex_assembly"] = name
            obj["blender_codex_source"] = str(path)
            imported.append(obj)
    except Exception:
        for obj in imported:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)
        raise

    material = bpy.data.materials.get("Glasses") or bpy.data.materials.new("Glasses")
    material.diffuse_color = (0.015, 0.015, 0.02, 1.0)
    bpy.context.view_layer.update()
    bounds_min = Vector((math.inf, math.inf, math.inf))
    bounds_max = Vector((-math.inf, -math.inf, -math.inf))
    for obj in imported:
        if not obj.data.materials:
            obj.data.materials.append(material)
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            bounds_min.x = min(bounds_min.x, world.x)
            bounds_min.y = min(bounds_min.y, world.y)
            bounds_min.z = min(bounds_min.z, world.z)
            bounds_max.x = max(bounds_max.x, world.x)
            bounds_max.y = max(bounds_max.y, world.y)
            bounds_max.z = max(bounds_max.z, world.z)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    for obj in imported:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = imported[0]
    bpy.context.view_layer.update()
    return {
        "name": name,
        "collection": collection.name,
        "parts": [obj.name for obj in imported],
        "location": list(arguments["location"]),
        "scale": float(arguments["scale"]),
        "bounds_min": list(bounds_min),
        "bounds_max": list(bounds_max),
        "size": list(bounds_max - bounds_min),
    }


def _point_to_polyline_distance(
    point: Vector,
    points: list[Vector],
    normal: Vector,
) -> tuple[float, float]:
    closest_planar = math.inf
    closest_offset = math.inf
    for index in range(len(points) - 1):
        start = points[index]
        segment = points[index + 1] - start
        length_squared = segment.length_squared
        if length_squared <= 1e-12:
            continue
        t = max(0.0, min(1.0, (point - start).dot(segment) / length_squared))
        closest = start + segment * t
        delta = point - closest
        offset = delta.dot(normal)
        planar = delta - normal * offset
        distance = planar.length
        if distance < closest_planar:
            closest_planar = distance
            closest_offset = abs(offset)
    return closest_planar, closest_offset


def _thicken_mouth_line(arguments: dict[str, Any]) -> dict[str, Any]:
    obj = bpy.data.objects.get(arguments["object_name"])
    if obj is None or obj.type != "MESH":
        raise ValueError(f"mesh object not found: {arguments['object_name']}")
    mesh = obj.data
    normal = Vector(arguments["normal"]).normalized()
    if normal.length < 0.5:
        raise ValueError("normal must not be zero")
    points = [Vector(point) for point in arguments["points"]]
    radius = float(arguments["radius"])
    amount = float(arguments["amount"])
    surface_window = float(arguments.get("surface_window", radius))
    normal_threshold = float(arguments.get("normal_threshold", 0.15))
    dry_run = bool(arguments.get("dry_run", False))
    inverse = obj.matrix_world.inverted()
    normal_matrix = obj.matrix_world.to_3x3()
    path_min = Vector(
        (
            min(point.x for point in points) - radius,
            min(point.y for point in points) - surface_window,
            min(point.z for point in points) - radius,
        )
    )
    path_max = Vector(
        (
            max(point.x for point in points) + radius,
            max(point.y for point in points) + surface_window,
            max(point.z for point in points) + radius,
        )
    )

    affected = 0
    max_displacement = 0.0
    bounds_min = Vector((math.inf, math.inf, math.inf))
    bounds_max = Vector((-math.inf, -math.inf, -math.inf))
    for vertex in mesh.vertices:
        world = obj.matrix_world @ vertex.co
        if (
            world.x < path_min.x
            or world.x > path_max.x
            or world.y < path_min.y
            or world.y > path_max.y
            or world.z < path_min.z
            or world.z > path_max.z
        ):
            continue
        vertex_normal = (normal_matrix @ vertex.normal).normalized()
        if vertex_normal.dot(normal) < normal_threshold:
            continue
        planar_distance, normal_distance = _point_to_polyline_distance(world, points, normal)
        if planar_distance > radius or normal_distance > surface_window:
            continue
        planar_falloff = 1.0 - planar_distance / radius
        normal_falloff = 1.0 - normal_distance / surface_window
        influence = (planar_falloff * planar_falloff) * max(0.0, normal_falloff)
        displacement = amount * influence
        if displacement <= 1e-7:
            continue
        if not dry_run:
            vertex.co = inverse @ (world + normal * displacement)
        affected += 1
        max_displacement = max(max_displacement, displacement)
        bounds_min.x = min(bounds_min.x, world.x)
        bounds_min.y = min(bounds_min.y, world.y)
        bounds_min.z = min(bounds_min.z, world.z)
        bounds_max.x = max(bounds_max.x, world.x)
        bounds_max.y = max(bounds_max.y, world.y)
        bounds_max.z = max(bounds_max.z, world.z)

    if affected == 0:
        raise ValueError("thicken_mouth_line did not affect any vertices")
    if not dry_run:
        mesh.update()
        obj["blender_codex_mouth_thickened"] = {
            "points": [list(point) for point in points],
            "normal": list(normal),
            "radius": radius,
            "amount": amount,
            "affected_vertices": affected,
        }
        bpy.context.view_layer.update()
    return {
        "object": obj.name,
        "affected_vertices": affected,
        "radius": radius,
        "amount": amount,
        "max_displacement": max_displacement,
        "dry_run": dry_run,
        "bounds_min": list(bounds_min),
        "bounds_max": list(bounds_max),
    }


def _render_workbench_preview(request_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    assert STATE is not None
    directory = STATE.runtime / "captures"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    view = arguments.get("view", "front")
    path = directory / f"{_safe_label(request_id)}-{view}.png"
    target = Vector(arguments.get("target", [0.0, 0.0, 0.0]))
    offset = {
        "front": Vector((0.0, -2.0, 0.0)),
        "back": Vector((0.0, 2.0, 0.0)),
        "left": Vector((-2.0, 0.0, 0.0)),
        "right": Vector((2.0, 0.0, 0.0)),
        "top": Vector((0.0, 0.0, 2.0)),
    }[view]
    camera_data = bpy.data.cameras.new("Bridge.Preview.Camera")
    camera = bpy.data.objects.new("Bridge.Preview.Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = target + offset
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = float(arguments.get("ortho_scale", 1.0))

    scene = bpy.context.scene
    shading = scene.display.shading
    previous = {
        "camera": scene.camera,
        "engine": scene.render.engine,
        "filepath": scene.render.filepath,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "file_format": scene.render.image_settings.file_format,
        "light": shading.light,
        "color_type": shading.color_type,
        "show_shadows": shading.show_shadows,
    }
    try:
        resolution = int(arguments.get("resolution", 800))
        scene.camera = camera
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.filepath = str(path)
        scene.render.resolution_x = resolution
        scene.render.resolution_y = resolution
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        shading.light = "STUDIO"
        shading.color_type = "MATERIAL"
        shading.show_shadows = True
        bpy.ops.render.render(write_still=True)
    finally:
        scene.camera = previous["camera"]
        scene.render.engine = previous["engine"]
        scene.render.filepath = previous["filepath"]
        scene.render.resolution_x = previous["resolution_x"]
        scene.render.resolution_y = previous["resolution_y"]
        scene.render.resolution_percentage = previous["resolution_percentage"]
        scene.render.image_settings.file_format = previous["file_format"]
        shading.light = previous["light"]
        shading.color_type = previous["color_type"]
        shading.show_shadows = previous["show_shadows"]
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(camera_data)
    return {"path": str(path), "kind": "workbench", "view": view}


def _execute(payload: dict[str, Any]) -> object:
    action = payload["action"]
    arguments = payload.get("arguments", {})
    if action == "get_scene_summary":
        return _scene_summary()
    if action == "get_mesh_components":
        return _mesh_components(arguments)
    if action == "transform_object":
        return _transform(arguments)
    if action == "undo":
        return {"result": bpy.ops.ed.undo()}
    if action == "save_checkpoint":
        return _save_checkpoint(arguments)
    if action == "capture_viewport":
        return _capture_viewport(payload["request_id"])
    if action == "create_hemisphere":
        return _create_hemisphere(arguments)
    if action == "create_image_relief":
        return _create_image_relief(arguments)
    if action == "import_stl_assembly":
        return _import_stl_assembly(arguments)
    if action == "thicken_mouth_line":
        return _thicken_mouth_line(arguments)
    if action == "render_workbench_preview":
        return _render_workbench_preview(payload["request_id"], arguments)
    raise ValueError(f"unsupported action: {action}")


def _process_queue() -> float:
    if STATE is None:
        return None
    for _ in range(5):
        try:
            command = STATE.commands.get_nowait()
        except queue.Empty:
            break
        try:
            if STATE.stopped:
                raise RuntimeError("emergency stop active")
            result = _execute(command.payload)
            command.response = {"ok": True, "request_id": command.payload["request_id"], "result": result}
        except Exception as exc:  # Blender errors must be returned to the caller.
            command.response = {"ok": False, "request_id": command.payload.get("request_id"), "error": str(exc)}
        finally:
            command.done.set()
            STATE.commands.task_done()
    return 0.05


def _write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def register() -> None:
    global STATE
    if STATE is not None:
        return
    runtime = _runtime_dir()
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    host = os.environ.get("BLENDER_CODEX_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Blender Codex Bridge only permits a loopback host")
    port = int(os.environ.get("BLENDER_CODEX_PORT", "9876"))
    server = ThreadingHTTPServer((host, port), RequestHandler)
    state = BridgeState(runtime=runtime, token=token, server=server)
    state.thread = threading.Thread(target=server.serve_forever, name="blender-codex-bridge", daemon=True)
    STATE = state
    _write_private(runtime / "token", token)
    _write_private(
        runtime / "connection.json",
        json.dumps({"url": f"http://{host}:{server.server_port}", "pid": os.getpid(), "protocol_version": PROTOCOL_VERSION}),
    )
    state.thread.start()
    bpy.app.timers.register(_process_queue, first_interval=0.05, persistent=True)
    print(f"[Blender Codex Bridge] listening on {host}:{server.server_port}")


def unregister() -> None:
    global STATE
    state = STATE
    STATE = None
    if state is None:
        return
    if bpy.app.timers.is_registered(_process_queue):
        bpy.app.timers.unregister(_process_queue)
    if state.server is not None:
        state.server.shutdown()
        state.server.server_close()
    if state.thread is not None:
        state.thread.join(timeout=2.0)
    for name in ("connection.json", "token"):
        try:
            (state.runtime / name).unlink()
        except FileNotFoundError:
            pass
    print("[Blender Codex Bridge] stopped")
