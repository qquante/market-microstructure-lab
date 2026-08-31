from __future__ import annotations

import json
import os
import socket
import urllib.parse
from typing import Any


DEFAULT_KEY = "bulk:paper_live:latest"
DEFAULT_CHANNEL = "bulk:paper_live:updates"


def redis_url() -> str:
    return os.environ.get("REDIS_URL", "").strip()


def redis_key() -> str:
    return os.environ.get("BULK_REDIS_LIVE_KEY", DEFAULT_KEY).strip() or DEFAULT_KEY


def redis_channel() -> str:
    return os.environ.get("BULK_REDIS_LIVE_CHANNEL", DEFAULT_CHANNEL).strip() or DEFAULT_CHANNEL


def _address(url: str) -> tuple[str, int, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "redis" or not parsed.hostname:
        raise ValueError("REDIS_URL must look like redis://host:6379/0")
    return parsed.hostname, parsed.port or 6379, parsed.password


def _pack(parts: list[str | bytes]) -> bytes:
    chunks = [f"*{len(parts)}\r\n".encode("ascii")]
    for part in parts:
        if isinstance(part, str):
            data = part.encode("utf-8")
        else:
            data = part
        chunks.append(f"${len(data)}\r\n".encode("ascii"))
        chunks.append(data)
        chunks.append(b"\r\n")
    return b"".join(chunks)


def _read_line(sock: socket.socket) -> bytes:
    data = bytearray()
    while True:
        char = sock.recv(1)
        if not char:
            raise ConnectionError("Redis closed the connection")
        data.extend(char)
        if data.endswith(b"\r\n"):
            return bytes(data[:-2])


def _read_response(sock: socket.socket) -> Any:
    prefix = sock.recv(1)
    if not prefix:
        raise ConnectionError("Redis closed the connection")
    if prefix in (b"+", b":"):
        return _read_line(sock).decode("utf-8")
    if prefix == b"-":
        raise RuntimeError(_read_line(sock).decode("utf-8"))
    if prefix == b"$":
        length = int(_read_line(sock))
        if length == -1:
            return None
        payload = bytearray()
        while len(payload) < length + 2:
            payload.extend(sock.recv(length + 2 - len(payload)))
        return bytes(payload[:-2])
    if prefix == b"*":
        length = int(_read_line(sock))
        return [_read_response(sock) for _ in range(length)]
    raise RuntimeError(f"Unknown Redis response prefix: {prefix!r}")


def command(url: str, *parts: str | bytes, timeout: float = 2.0) -> Any:
    host, port, password = _address(url)
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        if password:
            sock.sendall(_pack(["AUTH", password]))
            _read_response(sock)
        sock.sendall(_pack(list(parts)))
        return _read_response(sock)


def get_json(url: str | None = None, key: str | None = None) -> dict[str, Any] | None:
    url = (url or redis_url()).strip()
    if not url:
        return None
    payload = command(url, "GET", key or redis_key())
    if not payload:
        return None
    if isinstance(payload, bytes):
        return json.loads(payload.decode("utf-8"))
    return json.loads(str(payload))


def publish_json(payload: dict[str, Any], url: str | None = None, key: str | None = None, channel: str | None = None) -> None:
    url = (url or redis_url()).strip()
    if not url:
        return
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    command(url, "SET", key or redis_key(), data)
    command(url, "PUBLISH", channel or redis_channel(), data)
