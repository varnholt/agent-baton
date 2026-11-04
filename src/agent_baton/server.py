"""Tiny HTTP API over the store. Standard library only."""

from __future__ import annotations

import json
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .schema import FactType, ValidationError, validate_fact, validate_match
from .store import Store

MAX_BODY = 16 * 1024
MAX_WAIT = 55.0  # stay under common proxy idle timeouts; clients loop


class BatonServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, store: Store, types: dict[str, FactType]):
        super().__init__(addr, Handler)
        self.store = store
        self.types = types


class Handler(BaseHTTPRequestHandler):
    server: BatonServer
    server_version = "baton"
    sys_version = ""

    def log_message(self, fmt, *args):  # quieter, one line per request
        print(f"{self.address_string()} {fmt % args}", flush=True)

    # helpers ---------------------------------------------------------------

    def _send(self, status: int, body: object | None = None) -> None:
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def _member(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        return self.server.store.member_for_token(auth[7:].strip())

    def _query(self) -> dict[str, str]:
        return {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def _int(self, q: dict, key: str, default: int) -> int:
        try:
            return int(q.get(key, default))
        except ValueError:
            raise ValidationError(f"{key} must be an integer") from None

    # routes ----------------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        member = self._member()
        if member is None:
            return self._error(HTTPStatus.UNAUTHORIZED, "missing or unknown token")
        path = urlsplit(self.path).path.rstrip("/")
        try:
            if method == "GET" and path == "/v1/health":
                return self._send(200, {"ok": True, "member": member, "last_id": self.server.store.last_id()})
            if method == "GET" and path == "/v1/types":
                return self._send(200, [t.as_dict() for t in self.server.types.values()])
            if method == "POST" and path == "/v1/facts":
                return self._publish(member)
            if method == "GET" and path == "/v1/facts":
                q = self._query()
                facts = self.server.store.list(
                    q.get("type"), self._int(q, "after_id", 0), self._int(q, "limit", 100)
                )
                return self._send(200, facts)
            if method == "GET" and path.startswith("/v1/facts/"):
                try:
                    fact = self.server.store.get(int(path.rsplit("/", 1)[1]))
                except ValueError:
                    fact = None
                return self._send(200, fact) if fact else self._error(404, "no such fact")
            if method == "GET" and path == "/v1/wait":
                return self._wait()
            return self._error(HTTPStatus.NOT_FOUND, "no such route")
        except ValidationError as e:
            return self._error(HTTPStatus.BAD_REQUEST, str(e))

    def _publish(self, member: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body must be 1..{MAX_BODY} bytes")
        try:
            raw = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return self._error(HTTPStatus.BAD_REQUEST, "body is not JSON")
        fact = validate_fact(self.server.types, raw)
        stored = self.server.store.publish(fact, publisher=member)
        return self._send(HTTPStatus.CREATED, stored)

    def _wait(self) -> None:
        q = self._query()
        try:
            subject = json.loads(q.get("subject", "{}"))
        except json.JSONDecodeError:
            raise ValidationError("subject must be JSON") from None
        type_name, subject = validate_match(self.server.types, q.get("type"), subject)
        after_id = self._int(q, "after_id", 0)
        timeout = min(float(self._int(q, "timeout", 30)), MAX_WAIT)
        fact = self.server.store.wait(type_name, subject, after_id, timeout)
        if fact is None:
            return self._send(HTTPStatus.NO_CONTENT)
        return self._send(200, fact)


def serve(
    store: Store,
    types: dict[str, FactType],
    host: str = "127.0.0.1",
    port: int = 8765,
    tls_cert: str | None = None,
    tls_key: str | None = None,
) -> BatonServer:
    srv = BatonServer((host, port), store, types)
    if tls_cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls_cert, tls_key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv


__all__ = ["serve", "BatonServer"]
