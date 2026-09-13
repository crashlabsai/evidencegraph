"""Socket client and one-request CLI for the isolated registry.

Identity comes from the socket's OS credentials. The CLI accepts a JSON object
on stdin or as its only argument; the controller invokes it with docker exec.
"""

from __future__ import annotations

import json
import os
import socket
import sys

SOCKET = "/run/registry.sock"
MAX_REQUEST = 65_536
MAX_RESPONSE = 8 * 1024 * 1024


def exchange(payload: dict, *, socket_path: str | None = None, timeout: float = 120) -> dict:
    encoded = json.dumps(payload, allow_nan=False).encode() + b"\n"
    if len(encoded) > MAX_REQUEST:
        raise ValueError("registry request too large")
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(timeout)
        connection.connect(socket_path or os.environ.get("EG_REGISTRY_SOCKET", SOCKET))
        connection.sendall(encoded)
        with connection.makefile("rb") as stream:
            raw = stream.readline(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE or not raw.endswith(b"\n"):
        raise ValueError("registry response is missing, truncated or too large")
    response = json.loads(raw)
    if not isinstance(response, dict):
        raise ValueError("registry response must be an object")
    return response


def request(operation: str, **arguments: object) -> dict:
    response = exchange({"operation": operation, **arguments})
    if "error" in response:
        raise ValueError(response["error"])
    return response


def main() -> int:
    try:
        if len(sys.argv) > 2:
            raise ValueError("supply one JSON request, or send it on stdin")
        raw = sys.argv[1] if len(sys.argv) == 2 else sys.stdin.read(MAX_REQUEST + 1)
        if len(raw.encode()) > MAX_REQUEST:
            raise ValueError("registry request too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("registry request must be an object")
        response = exchange(payload)
    except Exception as exc:
        response = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(response, allow_nan=False))
    return 1 if "error" in response else 0


if __name__ == "__main__":
    raise SystemExit(main())
