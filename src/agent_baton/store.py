"""SQLite-backed fact log with blocking waits."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from .schema import NAME_RE, ValidationError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    name TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    subject TEXT NOT NULL,
    value TEXT NOT NULL,
    evidence TEXT,
    publisher TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_type ON facts(type, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "ts": r["ts"],
        "type": r["type"],
        "subject": json.loads(r["subject"]),
        "value": json.loads(r["value"]),
        "evidence": r["evidence"],
        "publisher": r["publisher"],
    }


class Store:
    def __init__(self, path: str, retention_days: int = 30):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._cond = threading.Condition(threading.Lock())
        self._retention = retention_days
        self._last_prune = 0.0
        with self._cond:
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._prune_locked()

    def close(self) -> None:
        with self._cond:
            self._db.close()

    # members -------------------------------------------------------------

    def add_member(self, name: str) -> str:
        if not NAME_RE.match(name):
            raise ValidationError(f"member name must match {NAME_RE.pattern!r}")
        token = "bt_" + secrets.token_urlsafe(32)
        with self._cond:
            try:
                self._db.execute(
                    "INSERT INTO members(name, token_hash, created) VALUES (?, ?, ?)",
                    (name, _hash(token), _now()),
                )
            except sqlite3.IntegrityError:
                raise ValidationError(f"member {name!r} already exists") from None
        return token

    def remove_member(self, name: str) -> bool:
        with self._cond:
            return self._db.execute("DELETE FROM members WHERE name = ?", (name,)).rowcount > 0

    def members(self) -> list[dict]:
        with self._cond:
            rows = self._db.execute("SELECT name, created FROM members ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def member_for_token(self, token: str) -> str | None:
        with self._cond:
            r = self._db.execute(
                "SELECT name FROM members WHERE token_hash = ?", (_hash(token),)
            ).fetchone()
        return r["name"] if r else None

    # facts ---------------------------------------------------------------

    def publish(self, fact: dict, publisher: str) -> dict:
        """Store an already-validated fact and wake every waiter."""
        with self._cond:
            cur = self._db.execute(
                "INSERT INTO facts(ts, type, subject, value, evidence, publisher)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _now(),
                    fact["type"],
                    json.dumps(fact["subject"], sort_keys=True),
                    json.dumps(fact["value"], sort_keys=True),
                    fact.get("evidence"),
                    publisher,
                ),
            )
            stored = _row(
                self._db.execute("SELECT * FROM facts WHERE id = ?", (cur.lastrowid,)).fetchone()
            )
            self._cond.notify_all()
            if time.monotonic() - self._last_prune > 3600:
                self._prune_locked()
        return stored

    def get(self, fact_id: int) -> dict | None:
        with self._cond:
            r = self._db.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return _row(r) if r else None

    def last_id(self) -> int:
        with self._cond:
            return self._db.execute("SELECT COALESCE(MAX(id), 0) FROM facts").fetchone()[0]

    def list(self, type_name: str | None = None, after_id: int = 0, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM facts WHERE id > ?", [after_id]
        if type_name:
            sql += " AND type = ?"
            args.append(type_name)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        with self._cond:
            rows = self._db.execute(sql, args).fetchall()
        return [_row(r) for r in rows]

    def _find_locked(self, type_name: str, subject: dict, after_id: int) -> dict | None:
        # Field names were validated against the type schema, so they are safe
        # to embed in a JSON path; values always go through parameters.
        sql = "SELECT * FROM facts WHERE type = ? AND id > ?"
        args: list = [type_name, after_id]
        for key, val in sorted(subject.items()):
            sql += " AND json_extract(subject, ?) = ?"
            args += [f"$.{key}", val]
        sql += " ORDER BY id DESC LIMIT 1"
        r = self._db.execute(sql, args).fetchone()
        return _row(r) if r else None

    def wait(self, type_name: str, subject: dict, after_id: int, timeout: float) -> dict | None:
        """Return the newest matching fact with id > after_id, blocking up to timeout."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                found = self._find_locked(type_name, subject, after_id)
                if found is not None:
                    return found
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def _prune_locked(self) -> None:
        self._last_prune = time.monotonic()
        if self._retention <= 0:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._db.execute("DELETE FROM facts WHERE ts < ?", (cutoff,))
