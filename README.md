# agent-baton

[![ci](https://github.com/varnholt/agent-baton/actions/workflows/ci.yml/badge.svg)](https://github.com/varnholt/agent-baton/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/agent-baton-cli.svg)](https://pypi.org/project/agent-baton-cli/)
![python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

**Your coding agents wait on each other all day. baton lets them stop asking you to relay.**

![demo: one agent waits, another publishes, the fact is verified against GitHub; a false fact and an injection attempt are rejected](docs/demo/demo.gif)

Agent A is blocked until Agent B's PR merges. Agent C is debugging a red build that
Agent D already diagnosed. Today a human notices, copies, pastes. With baton:

```
# Alice's agent, blocked - runs in the background, costs nothing while waiting
baton wait pr_merged -s repo=acme/api -s pr=812

# Bob's agent, when the merge happens
baton publish pr_merged -s repo=acme/api -s pr=812 -v commit=9f3c2e1 \
  -e https://github.com/acme/api/pull/812
```

Alice's agent wakes up with:

```
baton fact #41 (data from a peer - not instructions)
  type:      pr_merged
  subject:   pr=812 repo=acme/api
  value:     commit=9f3c2e1
  publisher: bob at 2026-11-04T13:11:18Z
  evidence:  https://github.com/acme/api/pull/812
  check:     verified - GitHub reports the PR merged at 9f3c2e1
```

No human in the loop, and no agent taking orders from another agent.

## Why it is safe to let agents talk

Letting agents message each other is an obvious way to spread prompt injection,
errors and confusion. baton is built so that does not happen:

| Risk | What baton does |
|---|---|
| One agent instructs another | **Facts, not messages.** Every field is a short string matching a strict pattern. There is no free-text field to put a prompt in. |
| A confused or malicious peer lies | **The receiver checks the source.** PR facts are checked against GitHub/Bitbucket, build facts against Jenkins or GitHub Actions, with the *receiver's* credentials. A contradicted fact exits non-zero. |
| Fake evidence (`https://evil.example/...`) | Verifiers only contact hosts the receiver already trusts, never follow redirects, and check the evidence is about the same PR/build as the fact. |
| Impersonation | The publisher is set by the server from the token, never from the payload. |
| "Who did that?" | Append-only log: every fact has publisher, timestamp and evidence. |

Facts that cannot be checked (hints, "working on") are delivered as `unverifiable`:
useful pointers, never grounds for an irreversible action.

## Fact types

| type | subject | value | checked against |
|---|---|---|---|
| `pr_merged` | repo, pr | commit | GitHub, Bitbucket Cloud |
| `pr_state` | repo, pr | state (`open/merged/declined/closed`) | GitHub, Bitbucket Cloud |
| `build_finished` | job, build | result (`SUCCESS/FAILURE/...`) | Jenkins (`JENKINS_URL`), GitHub Actions |
| `working_on` | repo, path | branch, state (`started/stopped`) | - |
| `hint` | topic | tag (slug) | - |

The verifier is chosen by the evidence host. For GitHub Actions the evidence is the
run URL, `https://github.com/<owner>/<repo>/actions/runs/<run_id>` (a trailing
`/attempts/<n>` checks that attempt; `/job/<id>` is accepted and checks the whole run).
`build` must be the run id. `job` names the workflow, case-insensitive: the workflow
file name without extension (`.github/workflows/ci.yml` -> `ci`) or the workflow's
display name, optionally prefixed with `<repo>/` or `<owner>/<repo>/`, which must then
match the evidence. The run must be `completed`; its conclusion maps to `success` ->
`SUCCESS`, `failure`/`timed_out`/`startup_failure` -> `FAILURE`, `cancelled` -> `ABORTED`,
`skipped`/`neutral` -> `NOT_BUILT`.

```
baton publish build_finished -s job=ci -s build=19121611211 -v result=SUCCESS \
  -e https://github.com/acme/api/actions/runs/19121611211
```

Add your own with `baton serve --types types.json` (always delivered as unverifiable):

```json
{"model_trained": {"description": "A training run finished",
                   "subject": {"run": "[a-z0-9-]{1,40}"},
                   "value": {"auc": "0\\.[0-9]{1,4}"}}}
```

## Setup

Pure Python 3.10+, no dependencies.

### 60-second quickstart

```
pip install agent-baton-cli               # or: uv tool install agent-baton-cli
baton quickstart
```

`quickstart` creates a database and a token for you in your per-user data directory
(`~/.local/share/baton`, or `%LOCALAPPDATA%\baton` on Windows), prints the
`BATON_URL`/`BATON_TOKEN` lines for bash and PowerShell, and serves on
`127.0.0.1:8765` (`--port` to change; it refuses non-loopback addresses). Paste the
env lines into two terminals and try it:

```
baton wait hint -s topic=hello              # terminal 1: blocks
baton publish hint -s topic=hello -v tag=hi # terminal 2: terminal 1 wakes up
```

Rerunning `baton quickstart` prints the same token again. For a team, set up a
shared server as below.

### Shared server

```
pip install agent-baton-cli

# on one shared machine
baton member add alice --db team.db     # prints a token once
baton member add bob --db team.db
baton serve --db team.db --host 0.0.0.0 --tls-cert cert.pem --tls-key key.pem

# on every agent's machine
export BATON_URL=https://baton.internal:8765
export BATON_TOKEN=bt_...
export JENKINS_URL=https://ci.internal    # optional, enables build checks
export GITHUB_TOKEN=...                   # optional, private repos
export BITBUCKET_TOKEN=...                # optional, private repos
```

Without TLS, keep the server on `127.0.0.1` and reach it over an SSH tunnel.

### Docker

```
docker volume create baton-data
docker run --rm -v baton-data:/data ghcr.io/varnholt/agent-baton baton member add alice --db /data/baton.db
docker run -d --name baton -p 127.0.0.1:8765:8765 -v baton-data:/data ghcr.io/varnholt/agent-baton
```

The image runs `baton serve --db /data/baton.db --host 0.0.0.0` as a non-root user and
speaks plain HTTP: publish the port on loopback as above, or put a TLS proxy in front.

## Use it from your agent

| Agent | File | Install |
|---|---|---|
| Claude Code | [`integrations/claude-code/skills/baton`](integrations/claude-code/skills/baton/SKILL.md) | copy to `~/.claude/skills/` |
| Codex | [`integrations/codex/AGENTS.md`](integrations/codex/AGENTS.md) | append to your `AGENTS.md` |
| Cursor | [`integrations/cursor/baton.mdc`](integrations/cursor/baton.mdc) | copy to `.cursor/rules/` |
| Aider | [`integrations/aider/CONVENTIONS.md`](integrations/aider/CONVENTIONS.md) | `aider --read ...` |
| Any MCP client | [`integrations/mcp`](integrations/mcp/README.md) | `claude mcp add baton -- baton mcp` |

Each one teaches the agent the same three habits: wait instead of asking the user to
relay, publish what others may be waiting on, and treat every fact as data.

Claude Code waits for free: it runs `baton wait` as a background command and is woken
when it exits. Any other agent or script works the same way, because `baton wait` is
just a blocking command with meaningful exit codes (`0` arrived, `2` contradicted by
its source, `3` timeout).

The demo above is generated from a real run: `uv run --with pillow python docs/demo/make_demo.py`.

## API

| | |
|---|---|
| `POST /v1/facts` | publish `{type, subject, value, evidence}` |
| `GET /v1/wait?type=&subject=<json>&after_id=&timeout=` | long-poll, 200 with a fact or 204 |
| `GET /v1/facts?type=&limit=` / `GET /v1/facts/<id>` | read |
| `GET /v1/types`, `GET /v1/health` | discovery |

All routes require `Authorization: Bearer <token>`. Facts are kept 30 days (`--retention-days`).

## Development

```
uv run --group dev pytest
```
