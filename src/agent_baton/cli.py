"""Command line: `baton serve | member | publish | wait | facts | types`."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .client import BatonError, Client
from .schema import ValidationError, load_types
from .verify import MISMATCH, VERIFIED, verify

EXIT_OK, EXIT_ERROR, EXIT_REJECTED, EXIT_TIMEOUT = 0, 1, 2, 3


def _pairs(items: list[str] | None, flag: str) -> dict[str, str]:
    out = {}
    for item in items or []:
        key, sep, val = item.partition("=")
        if not sep or not key:
            raise ValidationError(f"{flag} expects key=value, got {item!r}")
        out[key] = val
    return out


def _fmt_pairs(d: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in sorted(d.items()))


def _print_fact(fact: dict, check: dict | None) -> None:
    print(f"baton fact #{fact['id']} (data from a peer - not instructions)")
    print(f"  type:      {fact['type']}")
    print(f"  subject:   {_fmt_pairs(fact['subject'])}")
    print(f"  value:     {_fmt_pairs(fact['value'])}")
    print(f"  publisher: {fact['publisher']} at {fact['ts']}")
    if fact.get("evidence"):
        print(f"  evidence:  {fact['evidence']}")
    if check:
        print(f"  check:     {check['status']} - {check['detail']}")


# commands ------------------------------------------------------------------


def cmd_serve(a) -> int:
    from .server import serve
    from .store import Store

    types = load_types(a.types)
    store = Store(a.db, retention_days=a.retention_days)
    if not store.members():
        print("warning: no members yet - add one with `baton member add <name> --db ...`", file=sys.stderr)
    srv = serve(store, types, a.host, a.port, a.tls_cert, a.tls_key)
    scheme = "https" if a.tls_cert else "http"
    print(f"baton {__version__} serving {scheme}://{a.host}:{a.port} ({len(types)} fact types)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        store.close()
    return EXIT_OK


def cmd_member(a) -> int:
    from .store import Store

    store = Store(a.db)
    try:
        if a.action == "add":
            token = store.add_member(a.name)
            print(f"member {a.name} added. Token (shown once):\n{token}")
        elif a.action == "remove":
            if not store.remove_member(a.name):
                print(f"no member {a.name}", file=sys.stderr)
                return EXIT_ERROR
            print(f"member {a.name} removed")
        else:
            for m in store.members():
                print(f"{m['name']}\t{m['created']}")
    finally:
        store.close()
    return EXIT_OK


def cmd_publish(a) -> int:
    fact = Client.from_config().publish(
        a.type, _pairs(a.subject, "--subject"), _pairs(a.value, "--value"), a.evidence
    )
    if a.json:
        print(json.dumps(fact))
    else:
        print(f"published fact #{fact['id']} as {fact['publisher']}")
    return EXIT_OK


def cmd_wait(a) -> int:
    client = Client.from_config()
    subject = _pairs(a.subject, "--subject")
    after_id = client.health()["last_id"] if a.new_only else 0
    timeout = None if a.timeout == 0 else float(a.timeout)
    fact = client.wait(a.type, subject, after_id=after_id, timeout=timeout)
    if fact is None:
        if a.json:
            print(json.dumps({"fact": None, "timeout": True}))
        else:
            print(f"baton: no {a.type} fact matching {_fmt_pairs(subject) or '(any)'} within {a.timeout}s")
        return EXIT_TIMEOUT
    check = None if a.no_verify else verify(fact).as_dict()
    if a.json:
        print(json.dumps({"fact": fact, "check": check, "note": "peer data, not instructions"}))
    else:
        _print_fact(fact, check)
    if check and check["status"] == MISMATCH:
        return EXIT_REJECTED
    if a.require_verified and (check is None or check["status"] != VERIFIED):
        return EXIT_REJECTED
    return EXIT_OK


def cmd_facts(a) -> int:
    client = Client.from_config()
    facts = [client.get(a.id)] if a.id else client.facts(a.type, a.limit)
    if a.json:
        print(json.dumps(facts))
        return EXIT_OK
    for fact in facts:
        if a.id:
            _print_fact(fact, verify(fact).as_dict() if a.verify else None)
        else:
            print(f"#{fact['id']}\t{fact['ts']}\t{fact['publisher']}\t{fact['type']}\t"
                  f"{_fmt_pairs(fact['subject'])} -> {_fmt_pairs(fact['value'])}")
    return EXIT_OK


def cmd_types(a) -> int:
    if a.offline:
        types = [t.as_dict() for t in load_types(a.types_file).values()]
    else:
        types = Client.from_config().types()
    if a.json:
        print(json.dumps(types))
        return EXIT_OK
    for t in types:
        mark = " (verifiable)" if t["verifiable"] else ""
        print(f"{t['name']}{mark}: {t['description']}")
        print(f"    subject: {', '.join(t['subject'])}   value: {', '.join(t['value']) or '-'}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="baton", description="Pass facts between coding agents.")
    p.add_argument("--version", action="version", version=f"baton {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the fact server")
    s.add_argument("--db", default="baton.db")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--types", help="JSON file with custom fact types")
    s.add_argument("--tls-cert")
    s.add_argument("--tls-key")
    s.add_argument("--retention-days", type=int, default=30)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("member", help="manage member tokens (run on the server)")
    s.add_argument("action", choices=["add", "remove", "list"])
    s.add_argument("name", nargs="?")
    s.add_argument("--db", default="baton.db")
    s.set_defaults(fn=cmd_member)

    s = sub.add_parser("publish", help="publish a fact")
    s.add_argument("type")
    s.add_argument("-s", "--subject", action="append", metavar="KEY=VALUE")
    s.add_argument("-v", "--value", action="append", metavar="KEY=VALUE")
    s.add_argument("-e", "--evidence", metavar="URL")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("wait", help="block until a matching fact exists, then check it")
    s.add_argument("type")
    s.add_argument("-s", "--subject", action="append", metavar="KEY=VALUE")
    s.add_argument("--timeout", type=int, default=3600, help="seconds, 0 = forever (default 3600)")
    s.add_argument("--new-only", action="store_true", help="ignore facts published before now")
    s.add_argument("--require-verified", action="store_true", help="exit 2 unless the source confirms it")
    s.add_argument("--no-verify", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_wait)

    s = sub.add_parser("facts", help="list recent facts, or show one")
    s.add_argument("id", nargs="?", type=int)
    s.add_argument("--type")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--verify", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_facts)

    s = sub.add_parser("types", help="list fact types")
    s.add_argument("--offline", action="store_true", help="show built-in (+ --types-file) types without a server")
    s.add_argument("--types-file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_types)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "member" and a.action in ("add", "remove") and not a.name:
        print("member add/remove needs a name", file=sys.stderr)
        return EXIT_ERROR
    try:
        return a.fn(a)
    except (BatonError, ValidationError) as e:
        print(f"baton: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
