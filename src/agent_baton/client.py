"""HTTP client. Configuration comes from the environment or a JSON file.

BATON_URL    e.g. https://baton.internal:8765
BATON_TOKEN  the member token printed by `baton member add`
BATON_CA     optional CA bundle for a self-signed server certificate
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

CONFIG_PATH = Path(os.environ.get("BATON_CONFIG", Path.home() / ".config" / "baton" / "client.json"))


class BatonError(RuntimeError):
    pass


def load_config() -> dict:
    cfg: dict = {}
    if CONFIG_PATH.is_file():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for key in ("url", "token", "ca"):
        env = os.environ.get(f"BATON_{key.upper()}")
        if env:
            cfg[key] = env
    if not cfg.get("url") or not cfg.get("token"):
        raise BatonError(f"set BATON_URL and BATON_TOKEN, or write {CONFIG_PATH}")
    return cfg


class Client:
    def __init__(self, url: str, token: str, ca: str | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self.ctx = ssl.create_default_context(cafile=ca) if ca else None

    @classmethod
    def from_config(cls) -> "Client":
        cfg = load_config()
        return cls(cfg["url"], cfg["token"], cfg.get("ca"))

    def _call(self, method: str, path: str, body: object | None = None, timeout: float = 30):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", e.reason)
            except Exception:
                msg = e.reason
            raise BatonError(f"{e.code}: {msg}") from None
        except urllib.error.URLError as e:
            raise BatonError(f"cannot reach {self.url}: {e.reason}") from None

    def health(self) -> dict:
        return self._call("GET", "/v1/health")[1]

    def types(self) -> list[dict]:
        return self._call("GET", "/v1/types")[1]

    def publish(self, type_name: str, subject: dict, value: dict, evidence: str | None) -> dict:
        body = {"type": type_name, "subject": subject, "value": value}
        if evidence:
            body["evidence"] = evidence
        return self._call("POST", "/v1/facts", body)[1]

    def facts(self, type_name: str | None = None, limit: int = 20) -> list[dict]:
        q = {"limit": limit}
        if type_name:
            q["type"] = type_name
        return self._call("GET", "/v1/facts?" + urlencode(q))[1]

    def get(self, fact_id: int) -> dict:
        return self._call("GET", f"/v1/facts/{fact_id}")[1]

    def wait(self, type_name: str, subject: dict, after_id: int = 0, timeout: float | None = None) -> dict | None:
        """Long-poll until a matching fact exists. timeout=None waits forever."""
        deadline = None if timeout is None else time.monotonic() + timeout
        backoff = 1.0
        while True:
            window = 50.0
            if deadline is not None:
                window = min(window, deadline - time.monotonic())
                if window <= 0:
                    return None
            q = urlencode({
                "type": type_name,
                "subject": json.dumps(subject),
                "after_id": after_id,
                "timeout": max(0, int(window)),
            })
            try:
                status, fact = self._call("GET", "/v1/wait?" + q, timeout=window + 15)
            except BatonError as e:
                if not str(e).startswith("cannot reach"):
                    raise
                # Server restart or network blip: keep waiting, back off.
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            backoff = 1.0
            if status == 200 and fact:
                return fact
