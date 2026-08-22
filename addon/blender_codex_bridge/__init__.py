"""Stable Blender bootstrap with atomically reloadable modeling operations."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import queue
import secrets
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import bpy

from . import core as bundled_core


bl_info = {
    "name": "Blender Codex Bridge",
    "author": "NoctisLab",
    "version": (0, 4, 0),
    "blender": (4, 0, 0),
    "location": "Preferences > Add-ons",
    "description": "Stable local bridge with atomically reloadable modeling tools",
    "category": "Interface",
}

BOOTSTRAP_VERSION = "0.4.0"
CORE_API_VERSION = 1
PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 256 * 1024
COMMAND_TIMEOUT_SECONDS = 30.0
RELOAD_ACTION = "reload_core"
CORE: ModuleType = bundled_core


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


def _validate_reload_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ValueError("request_id must be a non-empty string")
    arguments = payload.get("arguments")
    if payload.get("action") != RELOAD_ACTION or not isinstance(arguments, dict):
        raise ValueError("invalid reload request")
    if set(arguments) != {"release_path", "sha256"}:
        raise ValueError("reload_core requires release_path and sha256")
    if not isinstance(arguments["release_path"], str) or len(arguments["release_path"]) > 4096:
        raise ValueError("release_path must be a string")
    digest = arguments["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("sha256 must be a lowercase hexadecimal digest")


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "BlenderCodexBootstrap/0.4"

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

    def do_GET(self) -> None:  # noqa: N802
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
                "bootstrap_version": BOOTSTRAP_VERSION,
                "core_version": getattr(CORE, "CORE_VERSION", "unknown"),
                "blender_version": bpy.app.version_string,
                "stopped": STATE.stopped,
                "queue_depth": STATE.commands.qsize(),
            },
        )

    def do_POST(self) -> None:  # noqa: N802
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
            if isinstance(payload, dict) and payload.get("action") == RELOAD_ACTION:
                _validate_reload_payload(payload)
            else:
                CORE._validate_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        command = PendingCommand(payload=payload)
        STATE.commands.put(command)
        if not command.done.wait(COMMAND_TIMEOUT_SECONDS):
            _json_response(self, HTTPStatus.GATEWAY_TIMEOUT, {"ok": False, "error": "command_timeout"})
            return
        _json_response(self, HTTPStatus.OK, command.response)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_core(path: Path, expected_sha256: str) -> ModuleType:
    assert STATE is not None
    releases = (STATE.runtime / "releases").resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(releases):
        raise ValueError("release_path must be inside the bridge runtime releases directory")
    if not resolved.is_file() or resolved.suffix != ".py":
        raise ValueError("release_path must reference a Python core file")
    if resolved.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("core release exceeds the 2 MiB safety limit")
    actual_sha256 = _sha256(resolved)
    if not secrets.compare_digest(actual_sha256, expected_sha256):
        raise ValueError("core release checksum mismatch")
    module_name = f"blender_codex_bridge_core_{actual_sha256[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not create a module specification for the core")
    candidate = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = candidate
    try:
        spec.loader.exec_module(candidate)
        if getattr(candidate, "CORE_API_VERSION", None) != CORE_API_VERSION:
            raise RuntimeError("core API version is incompatible with this bootstrap")
        if not callable(getattr(candidate, "self_test", None)):
            raise RuntimeError("core does not provide self_test")
        candidate.self_test()
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return candidate


def _write_current_release(path: Path, digest: str, version: str) -> None:
    assert STATE is not None
    state_path = STATE.runtime / "current-core.json"
    previous = None
    try:
        old = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(old, dict) and isinstance(old.get("current"), dict):
            old_current = old["current"]
            if old_current.get("sha256") == digest:
                previous = old.get("previous")
            else:
                previous = old_current
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    current = {"release_path": str(path), "sha256": digest, "core_version": version}
    payload = {"current": current, "previous": previous}
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(state_path)


def _reload_core(arguments: dict[str, Any]) -> dict[str, Any]:
    global CORE
    assert STATE is not None
    if not STATE.commands.empty():
        raise RuntimeError("core reload requires an otherwise empty command queue")
    path = Path(arguments["release_path"])
    candidate = _load_core(path, arguments["sha256"])
    previous_version = getattr(CORE, "CORE_VERSION", "unknown")
    candidate.STATE = STATE
    CORE = candidate
    _write_current_release(path.resolve(), arguments["sha256"], candidate.CORE_VERSION)
    return {
        "bootstrap_version": BOOTSTRAP_VERSION,
        "previous_core_version": previous_version,
        "core_version": candidate.CORE_VERSION,
        "sha256": arguments["sha256"],
    }


def _process_queue() -> float | None:
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
            if command.payload["action"] == RELOAD_ACTION:
                result = _reload_core(command.payload["arguments"])
            else:
                CORE.STATE = STATE
                result = CORE._execute(command.payload)
            command.response = {"ok": True, "request_id": command.payload["request_id"], "result": result}
        except Exception as exc:
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


def _load_persisted_core() -> None:
    global CORE
    assert STATE is not None
    state_path = STATE.runtime / "current-core.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        current = state["current"]
        candidate = _load_core(Path(current["release_path"]), current["sha256"])
        candidate.STATE = STATE
        CORE = candidate
    except FileNotFoundError:
        CORE = bundled_core
    except Exception as exc:
        CORE = bundled_core
        print(f"[Blender Codex Bridge] persisted core rejected; using bundled core: {exc}")
    CORE.STATE = STATE
    CORE.self_test()


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
    state.thread = threading.Thread(target=server.serve_forever, name="blender-codex-bootstrap", daemon=True)
    STATE = state
    _load_persisted_core()
    _write_private(runtime / "token", token)
    _write_private(
        runtime / "connection.json",
        json.dumps({"url": f"http://{host}:{server.server_port}", "pid": os.getpid(), "protocol_version": PROTOCOL_VERSION}),
    )
    state.thread.start()
    bpy.app.timers.register(_process_queue, first_interval=0.05, persistent=True)
    print(
        f"[Blender Codex Bridge] bootstrap {BOOTSTRAP_VERSION}, "
        f"core {getattr(CORE, 'CORE_VERSION', 'unknown')} listening on {host}:{server.server_port}"
    )


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
