# Atlassian Hermes Connector

Atlassian Hermes Connector is a Hermes plugin for Jira-backed Kanban work, local git branches, Hermes profile assignment, and reviewed GitHub or Bitbucket pull requests.

It is intentionally Hermes-native:

- `plugin.yaml`, `__init__.py`, `schemas.py`, and `tools.py` implement the plugin surface
- official Atlassian Jira/Rovo MCP is configured in Hermes, not bundled here
- Hermes Kanban state is stored locally as JSON
- local development uses `git` for branch, commit, and push planning
- branch names follow Jira-derived prefixes such as `bugfix/`, `feature/`, `hotfix/`, `docs/`, `test/`, or `chore/`
- a cron-safe sync script creates/updates Hermes Kanban tasks only for tickets assigned to a configured user/email
- PR creation requires a configured GitHub/Bitbucket MCP tool or an explicitly approved provider CLI fallback

## Quick Start

1. Copy `.env.example` into your Hermes environment manager.
2. Configure official Atlassian MCP in `~/.hermes/config.yaml`.
3. Copy `config/repositories.example.json` to a private path and set `HERMES_REPO_MAP`.
4. Install this repo as a Hermes plugin under `~/.hermes/plugins/atlassian-hermes-connector`.
5. Enable it:

```bash
hermes plugins enable atlassian-hermes-connector
```

6. Verify local readiness:

```bash
python3 scripts/ticket.py status
```

## Core Workflow

1. Use Atlassian MCP to create or fetch one Jira issue in the configured project.
2. Call `kanban_ensure_board` to create the Hermes project and Kanban board.
3. Call `kanban_create_item` with the Jira issue JSON.
4. Assign a Hermes profile with `kanban_assign_profile`.
5. Resolve the local repo with `repository_resolve`.
6. Start a git branch with `work_start_branch`.
7. Read `CLAUDE.md`, `AGENT.md`, and `README.md` in the target repo before editing.
8. Commit with `work_commit`.
9. After user review, use `pr_plan` and then the configured provider MCP or approved CLI.

## Cron Sync

Use `scripts/jira_cron_sync.py` with Jira issue JSON exported through official Atlassian MCP:

```bash
python3 scripts/jira_cron_sync.py \
  --issues-json /tmp/hermes-assigned-jira.json \
  --assignee you@example.com \
  --profile default
```

See `config/cron.example` for a crontab template.

## Validation

```bash
python3 -m unittest discover -s tests
env PYTHONPYCACHEPREFIX=/private/tmp/atlassian-hermes-pycache \
  python3 -m py_compile __init__.py schemas.py tools.py workflow.py scripts/ticket.py
```
