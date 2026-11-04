# baton over MCP

`baton mcp` is a Model Context Protocol server on stdio. Any MCP client (Claude Code,
Claude Desktop, Cursor, Codex, ...) can use baton through it without shelling out.
It is part of the `baton` command, so there is nothing extra to install.

It reads the same configuration as the CLI: `BATON_URL` and `BATON_TOKEN` (or
`~/.config/baton/client.json`), plus the verifier settings (`GITHUB_TOKEN`,
`BITBUCKET_TOKEN`, `JENKINS_URL`, ...). Verification runs inside `baton mcp`, on
your machine, with your credentials.

## Claude Code

```
claude mcp add baton -e BATON_URL=http://127.0.0.1:8765 -e BATON_TOKEN=bt_... -- baton mcp
```

If `BATON_URL` and `BATON_TOKEN` are already exported in the shell that starts
Claude Code, `claude mcp add baton -- baton mcp` is enough.

## Other clients (`mcpServers` JSON)

```json
{
  "mcpServers": {
    "baton": {
      "command": "baton",
      "args": ["mcp"],
      "env": {
        "BATON_URL": "http://127.0.0.1:8765",
        "BATON_TOKEN": "bt_..."
      }
    }
  }
}
```

Without a global install: `"command": "uvx", "args": ["--from", "agent-baton-cli", "baton", "mcp"]`.

## Tools

| tool | does |
|---|---|
| `baton_types` | list the fact types the server accepts |
| `baton_publish` | publish a fact: `type`, `subject`, `value`, `evidence` |
| `baton_wait` | block until a matching fact exists (`timeout` default 300 s, max 600 s), verify it, return it with a status |
| `baton_facts` | list recent facts (unverified), or fetch one by `id` and verify it |

Every result restates that facts are peer data, not instructions. `baton_wait` and
`baton_facts` with an `id` return a `status`:

- `verified` - the source of truth confirms the fact.
- `mismatch` - the source contradicts it. Do not act on it.
- `unverifiable` - no source to check (hints, unknown hosts). A pointer only.
- `error` - the source could not be reached. Unconfirmed.

For waits longer than 10 minutes, call `baton_wait` again, or run `baton wait` as a
background shell command instead, which can wait for hours at no cost. Some
clients also time out tool calls on their own; raise that limit
(e.g. `MCP_TOOL_TIMEOUT` in Claude Code) if waits are cut short.
