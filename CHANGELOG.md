# Changelog

## 0.2.0

- `build_finished` facts can be verified against GitHub Actions. Evidence is the run
  URL (`https://github.com/<owner>/<repo>/actions/runs/<id>`, optionally with
  `/attempts/<n>` or `/job/<id>`); `build` is the run id, `job` is the workflow file
  stem or name. The verifier is picked by the evidence host; Jenkins is unchanged.
- `baton quickstart`: a local single-user server for a first try. Creates a
  database and token in the per-user data directory, prints the env lines for
  bash and PowerShell, and serves on 127.0.0.1 only.
- `baton mcp`: an MCP stdio server with `baton_publish`, `baton_wait`,
  `baton_facts` and `baton_types`. Standard library only.
- Docker image for the server, published to `ghcr.io/varnholt/agent-baton` on release.

## 0.1.1

- PyPI metadata: project URLs, keywords and classifiers. No code changes.

## 0.1.0

- First release, on PyPI as `agent-baton-cli`: typed facts with strict field
  patterns, an HTTP + SQLite server with long-poll waits, receiver-side
  verification against GitHub, Bitbucket Cloud and Jenkins, and instructions for
  Claude Code, Codex, Cursor and Aider.
