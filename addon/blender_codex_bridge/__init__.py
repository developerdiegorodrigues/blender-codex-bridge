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


bl_info = {
    "name": "Blender Codex Bridge",
    "author": "NoctisLab",
    "version": (0, 2, 0),
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


def _execute(payload: dict[str, Any]) -> object:
    action = payload["action"]
    arguments = payload.get("arguments", {})
    if action == "get_scene_summary":
        return _scene_summary()
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
