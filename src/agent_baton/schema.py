"""Fact types and validation.

A fact is structured data, never prose: every field is a short string that must
fully match a per-field pattern. This is the core safety property of baton - a
peer can tell you *that* something happened, but it cannot hand your agent a
paragraph of instructions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

SLUG = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}"
INT = r"[0-9]{1,10}"
SHA = r"[0-9a-f]{7,64}"
TAG = r"[a-z0-9][a-z0-9-]{0,63}"

NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
MAX_EVIDENCE = 500


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class FactType:
    name: str
    description: str
    subject: dict[str, str]
    value: dict[str, str]
    verifiable: bool = False
    evidence_required: bool = False
    builtin: bool = field(default=True, compare=False)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "subject": self.subject,
            "value": self.value,
            "verifiable": self.verifiable,
            "evidence_required": self.evidence_required,
        }


BUILTIN_TYPES: dict[str, FactType] = {
    t.name: t
    for t in [
        FactType(
            "pr_merged",
            "A pull request was merged. Evidence: the PR URL on github.com or bitbucket.org.",
            subject={"repo": SLUG, "pr": INT},
            value={"commit": SHA},
            verifiable=True,
            evidence_required=True,
        ),
        FactType(
            "pr_state",
            "A pull request changed state. Evidence: the PR URL on github.com or bitbucket.org.",
            subject={"repo": SLUG, "pr": INT},
            value={"state": r"open|merged|declined|closed"},
            verifiable=True,
            evidence_required=True,
        ),
        FactType(
            "build_finished",
            "A CI build finished. Evidence: the Jenkins build URL.",
            subject={"job": SLUG, "build": INT},
            value={"result": r"SUCCESS|FAILURE|UNSTABLE|ABORTED|NOT_BUILT"},
            verifiable=True,
            evidence_required=True,
        ),
        FactType(
            "working_on",
            "An agent started or stopped working in a repo path (collision avoidance).",
            subject={"repo": SLUG, "path": SLUG},
            value={"branch": SLUG, "state": r"started|stopped"},
        ),
        FactType(
            "hint",
            "An unverifiable pointer, e.g. a suspected root cause. Tag + evidence link only.",
            subject={"topic": SLUG},
            value={"tag": TAG},
        ),
    ]
}


def _check_name(kind: str, name: object) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValidationError(f"{kind} must match {NAME_RE.pattern!r}")
    return name


def _check_fields(kind: str, spec: dict[str, str], data: object, partial: bool) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValidationError(f"{kind} must be an object")
    unknown = sorted(set(data) - set(spec))
    if unknown:
        raise ValidationError(f"{kind}: unknown field(s) {unknown}; allowed {sorted(spec)}")
    if not partial:
        missing = sorted(set(spec) - set(data))
        if missing:
            raise ValidationError(f"{kind}: missing field(s) {missing}")
    out = {}
    for key, val in data.items():
        if not isinstance(val, str) or not re.fullmatch(spec[key], val):
            raise ValidationError(f"{kind}.{key}: value does not match {spec[key]!r}")
        out[key] = val
    return out


def check_evidence(url: object) -> str:
    if not isinstance(url, str) or not url or len(url) > MAX_EVIDENCE:
        raise ValidationError(f"evidence must be a URL of at most {MAX_EVIDENCE} characters")
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        raise ValidationError("evidence must not contain whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValidationError("evidence must be an http(s) URL")
    return url


def validate_fact(types: dict[str, FactType], fact: object) -> dict:
    if not isinstance(fact, dict):
        raise ValidationError("fact must be an object")
    unknown = sorted(set(fact) - {"type", "subject", "value", "evidence"})
    if unknown:
        raise ValidationError(f"unknown top-level field(s) {unknown}")
    ftype = types.get(fact.get("type"))  # type: ignore[arg-type]
    if ftype is None:
        raise ValidationError(f"unknown fact type {fact.get('type')!r}; known {sorted(types)}")
    evidence = fact.get("evidence")
    if evidence is None:
        if ftype.evidence_required:
            raise ValidationError(f"{ftype.name} requires evidence")
    else:
        evidence = check_evidence(evidence)
    return {
        "type": ftype.name,
        "subject": _check_fields("subject", ftype.subject, fact.get("subject", {}), partial=False),
        "value": _check_fields("value", ftype.value, fact.get("value", {}), partial=False),
        "evidence": evidence,
    }


def validate_match(types: dict[str, FactType], type_name: object, subject: object) -> tuple[str, dict]:
    ftype = types.get(type_name)  # type: ignore[arg-type]
    if ftype is None:
        raise ValidationError(f"unknown fact type {type_name!r}; known {sorted(types)}")
    return ftype.name, _check_fields("subject", ftype.subject, subject, partial=True)


def load_types(path: str | Path | None) -> dict[str, FactType]:
    """Built-in types plus custom ones from a JSON file.

    Custom types are never verifiable: baton only ships verifiers it can reason about.
    """
    types = dict(BUILTIN_TYPES)
    if path is None:
        return types
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValidationError("types file must be a JSON object of name -> spec")
    for name, spec in raw.items():
        _check_name("type name", name)
        if name in BUILTIN_TYPES:
            raise ValidationError(f"custom type {name!r} shadows a built-in type")
        if not isinstance(spec, dict):
            raise ValidationError(f"type {name}: spec must be an object")
        fields: dict[str, dict[str, str]] = {}
        for part in ("subject", "value"):
            part_spec = spec.get(part, {})
            if not isinstance(part_spec, dict):
                raise ValidationError(f"type {name}.{part} must be an object")
            for key, pattern in part_spec.items():
                _check_name(f"type {name}.{part} field", key)
                if not isinstance(pattern, str):
                    raise ValidationError(f"type {name}.{part}.{key}: pattern must be a string")
                re.compile(pattern)
            fields[part] = dict(part_spec)
        if not fields["subject"]:
            raise ValidationError(f"type {name} needs at least one subject field")
        types[name] = FactType(
            name,
            str(spec.get("description", ""))[:200],
            subject=fields["subject"],
            value=fields["value"],
            builtin=False,
        )
    return types
