"""Check a fact against its source of truth, from the *receiver's* side.

Rules that keep this safe:
- Only hosts the receiver already trusts are contacted (github.com,
  bitbucket.org, and the receiver's own JENKINS_URL). An evidence URL on any
  other host is "unverifiable", never fetched - otherwise a peer could point
  the verifier at a fake API, or harvest the credentials it sends.
- The evidence must describe the same subject as the fact. A real, merged PR
  does not vouch for a different PR number.
- Credentials come from the receiver's environment and are only sent to the
  host they belong to.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

VERIFIED = "verified"
MISMATCH = "mismatch"
UNVERIFIABLE = "unverifiable"
ERROR = "error"

GITHUB_PR = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)(?:/[\w/-]*)?/?\Z")
BITBUCKET_PR = re.compile(
    r"https://bitbucket\.org/([\w.-]+)/([\w.-]+)/pull-requests/(\d+)(?:/[\w/-]*)?/?\Z"
)


@dataclass(frozen=True)
class Verification:
    status: str
    detail: str

    def as_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib copies Authorization onto redirects, even to another host.
    # A verifier must never follow one: fail instead.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"refusing redirect to {newurl}", headers, fp)


_opener = urllib.request.build_opener(_NoRedirect)


def _fetch_json(url: str, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with _opener.open(req, timeout=15) as resp:
        return json.loads(resp.read())


# Tests replace this.
fetch_json = _fetch_json


def _same_repo(subject_repo: str, owner: str, repo: str) -> bool:
    s = subject_repo.lower()
    return s in (repo.lower(), f"{owner}/{repo}".lower())


def _same_commit(a: str | None, b: str) -> bool:
    if not a:
        return False
    a, b = a.lower(), b.lower()
    return min(len(a), len(b)) >= 7 and (a.startswith(b) or b.startswith(a))


def _pr_info(evidence: str, subject: dict) -> tuple[str, str, str] | Verification:
    """Return (state, merge_commit, host) for a PR URL, or a Verification on failure."""
    m = GITHUB_PR.match(evidence)
    host = "github"
    if not m:
        m = BITBUCKET_PR.match(evidence)
        host = "bitbucket"
    if not m:
        return Verification(UNVERIFIABLE, "evidence is not a github.com or bitbucket.org PR URL")
    owner, repo, number = m.groups()
    if not _same_repo(subject["repo"], owner, repo) or subject["pr"] != number:
        return Verification(MISMATCH, f"evidence is {owner}/{repo}#{number}, fact is about {subject['repo']}#{subject['pr']}")

    if host == "github":
        headers = {}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        data = fetch_json(f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/pulls/{number}", headers)
        state = "merged" if data.get("merged") else data.get("state", "")
        return state, data.get("merge_commit_sha") or "", "GitHub"

    headers = {}
    if os.environ.get("BITBUCKET_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['BITBUCKET_TOKEN']}"
    elif os.environ.get("BITBUCKET_USER") and os.environ.get("BITBUCKET_APP_PASSWORD"):
        pair = f"{os.environ['BITBUCKET_USER']}:{os.environ['BITBUCKET_APP_PASSWORD']}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
    data = fetch_json(
        f"https://api.bitbucket.org/2.0/repositories/{quote(owner)}/{quote(repo)}/pullrequests/{number}", headers
    )
    state = str(data.get("state", "")).lower()
    if state == "superseded":
        state = "declined"
    commit = (data.get("merge_commit") or {}).get("hash") or ""
    return state, commit, "Bitbucket"


def _verify_pr_merged(fact: dict) -> Verification:
    info = _pr_info(fact["evidence"], fact["subject"])
    if isinstance(info, Verification):
        return info
    state, commit, host = info
    if state != "merged":
        return Verification(MISMATCH, f"{host} reports the PR as {state or 'unknown'}, not merged")
    if not _same_commit(commit, fact["value"]["commit"]):
        return Verification(MISMATCH, f"{host} merge commit {commit[:12] or '?'} != {fact['value']['commit']}")
    return Verification(VERIFIED, f"{host} reports the PR merged at {commit[:12]}")


def _verify_pr_state(fact: dict) -> Verification:
    info = _pr_info(fact["evidence"], fact["subject"])
    if isinstance(info, Verification):
        return info
    state, _, host = info
    want = fact["value"]["state"]
    if state != want:
        return Verification(MISMATCH, f"{host} reports {state or 'unknown'}, fact says {want}")
    return Verification(VERIFIED, f"{host} reports the PR as {state}")


def _jenkins_job_from_path(path: str, base_path: str) -> tuple[str, str] | None:
    rest = path[len(base_path):].strip("/").split("/")
    if len(rest) < 3 or len(rest) % 2 != 1:
        return None
    *pairs, build = rest
    if not build.isdigit() or any(pairs[i] != "job" for i in range(0, len(pairs), 2)):
        return None
    job = "/".join(pairs[i + 1] for i in range(0, len(pairs), 2))
    return job, build


def _verify_build_finished(fact: dict) -> Verification:
    base = os.environ.get("JENKINS_URL", "").rstrip("/")
    if not base:
        return Verification(UNVERIFIABLE, "JENKINS_URL is not set on this side")
    ev, b = urlsplit(fact["evidence"]), urlsplit(base)
    if (ev.scheme, ev.netloc) != (b.scheme, b.netloc) or not ev.path.startswith(b.path):
        return Verification(UNVERIFIABLE, f"evidence is not on the trusted Jenkins {base}")
    parsed = _jenkins_job_from_path(ev.path, b.path)
    if parsed is None:
        return Verification(UNVERIFIABLE, "evidence is not a Jenkins build URL (.../job/<name>/<number>/)")
    job, build = parsed
    subject = fact["subject"]
    if job.lower() != subject["job"].lower() or build != subject["build"]:
        return Verification(MISMATCH, f"evidence is {job} #{build}, fact is about {subject['job']} #{subject['build']}")
    headers = {}
    if os.environ.get("JENKINS_USER") and os.environ.get("JENKINS_TOKEN"):
        pair = f"{os.environ['JENKINS_USER']}:{os.environ['JENKINS_TOKEN']}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
    api = f"{b.scheme}://{b.netloc}{ev.path.rstrip('/')}/api/json?tree=number,result,building"
    data = fetch_json(api, headers)
    if data.get("building"):
        return Verification(MISMATCH, "Jenkins reports the build is still running")
    if str(data.get("number")) != build:
        return Verification(MISMATCH, f"Jenkins returned build {data.get('number')}")
    if data.get("result") != fact["value"]["result"]:
        return Verification(MISMATCH, f"Jenkins reports {data.get('result')}, fact says {fact['value']['result']}")
    return Verification(VERIFIED, f"Jenkins reports {job} #{build} {data.get('result')}")


VERIFIERS = {
    "pr_merged": _verify_pr_merged,
    "pr_state": _verify_pr_state,
    "build_finished": _verify_build_finished,
}


def verify(fact: dict) -> Verification:
    fn = VERIFIERS.get(fact["type"])
    if fn is None:
        return Verification(UNVERIFIABLE, f"{fact['type']} facts cannot be checked; treat as a hint")
    if not fact.get("evidence"):
        return Verification(UNVERIFIABLE, "no evidence attached")
    try:
        return fn(fact)
    except urllib.error.HTTPError as e:
        return Verification(ERROR, f"source returned HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return Verification(ERROR, f"cannot reach source: {getattr(e, 'reason', e)}")
    except (ValueError, KeyError, TypeError) as e:
        return Verification(ERROR, f"unexpected response from source: {e}")
