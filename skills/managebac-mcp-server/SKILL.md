---
name: managebac-mcp-server
description: Run and troubleshoot the ManageBac MCP server in this repository, including local MySQL bootstrap, startup sync, and MCP transport startup for Codex. Use when the user needs to install, start, verify, or debug the ManageBac MCP server integration.
---

# ManageBac MCP Server

Use this skill to run and validate the ManageBac MCP server from this repository.

## Quick start

1. Ensure runtime files exist:
- `config/managebac.yaml`
- `.env`

2. Start local MySQL:
```bash
make db-up
make db-wait
```

3. Run one-time data sync:
```bash
PYTHONPATH=src python3 -m managebac_mcp.main --sync-only
```

4. Run MCP server over stdio:
```bash
PYTHONPATH=src python3 -m managebac_mcp.main --transport stdio
```

## Codex MCP config snippet

Add this block to `~/.codex/config.toml`:

```toml
[mcp_servers.managebac]
command = "bash"
args = ["-lc", "set -a; source .env; set +a; PYTHONPATH=src python3 -m managebac_mcp.main --transport stdio"]
cwd = "/Users/artyomkonukhov/Projects/manageback-mcp"
```

## Diagnostics

### Verify tests
```bash
PYTHONPATH=src PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m pytest -q
```

### Verify login and browser runtime
```bash
python3 -m playwright install chromium
```

### Verify DB connectivity

MySQL mode (from `.env`):
```bash
make db-wait
```

SQLite fallback mode:
```bash
DATABASE_URL=sqlite+pysqlite:////tmp/managebac_mcp.db PYTHONPATH=src python3 -m managebac_mcp.main --sync-only
```

## Known failure patterns

- `AUTH_MISSING_CREDENTIALS`: set `MANAGEBAC_LOGIN` and `MANAGEBAC_PASSWORD` in `.env`.
- `Connection refused` to MySQL: run `make db-up` and `make db-wait`.
- Playwright missing browser executable: run `python3 -m playwright install chromium`.
- UI selector mismatch after ManageBac update: adjust selectors in `config/managebac.yaml`.
