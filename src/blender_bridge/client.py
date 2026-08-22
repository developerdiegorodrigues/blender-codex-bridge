"""Small standard-library HTTP client for the local bridge."""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .protocol import Command, PROTOCOL_VERSION
from .runtime import RuntimePaths


class BridgeClient:
    def __init__(self, runtime: RuntimePaths | None = None, timeout: float = 35.0):
        self.runtime = runtime or RuntimePaths.discover()
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: object | None = None) -> Any:
        connection = self.runtime.connection()
        url = str(connection["url"]).rstrip("/") + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.runtime.token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"bridge returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"could not connect to Blender bridge: {exc.reason}") from exc

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def command(self, action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        command = Command(
            request_id=str(uuid.uuid4()),
            action=action,
            arguments=arguments or {},
        )
        payload = command.as_dict()
        payload["protocol_version"] = PROTOCOL_VERSION
        return self._request("POST", "/v1/commands", payload)
