# managebac-mcp

Config-driven MCP server for ManageBac student workflows via browser automation (Playwright), with local MySQL cache.

## What is implemented

- Config file for routes/selectors/timeouts/feature flags (`config/managebac.yaml` expected, sample provided)
- Credentials from env vars (`MANAGEBAC_LOGIN`, `MANAGEBAC_PASSWORD`)
- Startup sync that collects:
  - classes (`/student/classes/my`)
  - class tasks (`/student/classes/{class_id}/core_tasks`)
  - CAS experiences (`/student/ib/activity/cas`)
- Local DB model (MySQL-compatible SQLAlchemy schema) for classes, tasks, submissions, CAS, sync runs, snapshots
- Read and action tool split for all requested areas (classes/tasks/dropbox/CAS/reflections)
- Task file submission flow (dropbox upload)
- CAS actions: create experience + add reflections (journal/file/video/website/photos)
- Unit tests for config, sync, reads, submissions, CAS actions using fake browser gateway

## Project structure

- `config/managebac.example.yaml` - sample config with all route templates and selectors
- `src/managebac_mcp/config.py` - settings + config loader + URL builder
- `src/managebac_mcp/schema.py` - DB schema
- `src/managebac_mcp/repositories.py` - data access layer
- `src/managebac_mcp/browser.py` - browser gateway interface + Playwright implementation
- `src/managebac_mcp/services.py` - sync/read/action services
- `src/managebac_mcp/server.py` - MCP tool registration
- `src/managebac_mcp/main.py` - CLI entrypoint
- `tests/` - unit tests

## Setup

1. Create runtime config:

```bash
cp config/managebac.example.yaml config/managebac.yaml
```

2. Create `.env` from sample:

```bash
cp .env.example .env
```

3. Install dependencies:

```bash
python3 -m pip install -e .
python3 -m pip install -e '.[server,test]'
python3 -m playwright install chromium
```

4. Start local MySQL (auto-setup via Docker Compose):

```bash
make db-up
make db-wait
```

The app creates tables automatically on startup (`Base.metadata.create_all`).

For quick local smoke without MySQL daemon, set `DATABASE_URL=sqlite+pysqlite:///tmp/managebac_mcp.db`.

## Run

Run one-time startup sync:

```bash
PYTHONPATH=src python3 -m managebac_mcp.main --sync-only
```

Optional: run with SQLite override for local checks:

```bash
DATABASE_URL=sqlite+pysqlite:////tmp/managebac_mcp.db PYTHONPATH=src python3 -m managebac_mcp.main --sync-only
```

Run MCP server via stdio:

```bash
PYTHONPATH=src python3 -m managebac_mcp.main --transport stdio
```

Run MCP server via streamable HTTP:

```bash
PYTHONPATH=src python3 -m managebac_mcp.main --transport streamable-http --host 127.0.0.1 --port 3001
```

## Implemented MCP tools

### Auth

- `read_auth_status`
- `action_login`

### Startup sync

- `action_startup_sync`

### Classes

- `read_classes`
- `action_refresh_classes`
- `read_class_details`

### Tasks

- `read_class_tasks`
- `action_refresh_class_tasks`
- `read_task`
- `read_task_dropbox`

### Submission

- `action_submit_task_file`
- `read_submission_result`
- `action_retry_submission`

### CAS

- `read_cas_dashboard`
- `action_refresh_cas`
- `read_cas_experience`
- `action_create_cas_experience`
- `read_cas_reflections`

### CAS reflections/evidence

- `action_add_reflection_journal`
- `action_add_reflection_file`
- `action_add_reflection_video`
- `action_add_reflection_website`
- `action_add_reflection_photos`

## Notes

- All route paths and selectors are configurable in YAML.
- If UI changes, update selectors/routes in config instead of changing code.
- Submission/CAS actions save artifacts (screenshot/html) for diagnostics.
- 2FA/captcha handling is not implemented (as requested, assumed absent).

## Tests

Run tests:

```bash
PYTHONPATH=src PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m pytest -q
```

Current status:

- `5 passed`

## Dev DB commands

```bash
make db-up      # start mysql container
make db-wait    # wait until mysql is reachable from .env params
make db-logs    # tail mysql logs
make db-down    # stop and remove compose services
```
