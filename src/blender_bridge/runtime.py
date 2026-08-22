"""Runtime paths shared by the CLI and Blender add-on."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


def default_runtime_dir() -> Path:
    configured = os.environ.get("BLENDER_CODEX_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    return Path(tempfile.gettempdir()) / f"blender-codex-bridge-{uid}"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path

    @classmethod
    def discover(cls) -> "RuntimePaths":
        return cls(default_runtime_dir())

    @property
    def stop_file(self) -> Path:
        return self.root / "STOP"

    @property
    def connection_file(self) -> Path:
        return self.root / "connection.json"

    @property
    def token_file(self) -> Path:
        return self.root / "token"

    def ensure(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def connection(self) -> dict[str, object]:
        try:
            value = json.loads(self.connection_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError("Blender bridge connection file was not found") from exc
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            raise RuntimeError("Blender bridge connection file is invalid")
        return value

    def token(self) -> str:
        try:
            return self.token_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RuntimeError("Blender bridge token was not found") from exc
