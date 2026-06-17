# Atlassian Hermes Connector

Atlassian Hermes Connector is a Hermes plugin for Jira-backed Kanban work, local git branches, Hermes profile assignment, and reviewed GitHub or Bitbucket pull requests.

It is intentionally Hermes-native:

- `plugin.yaml`, `__init__.py`, `schemas.py`, and `tools.py` implement the plugin surface
- official Atlassian Jira/Rovo MCP is configured in Hermes, not bundled here
- Hermes Kanban state is stored locally as JSON
- local development uses `git` for branch, commit, and push planning
- branch names follow Jira-derived prefixes such as `bugfix/`, `feature/`, `hotfix/`, `docs/`, `test/`, or `chore/`
- a cron-safe sync script creates/updates Hermes Kanban tasks only for tickets assigned to a configured user/email
- Jira sync moves Hermes Kanban items according to Jira status, imports comments, and marks active blockers from Jira issue links
- Atlassian MCP tool auto-discovery maps available Jira MCP tools to canonical plugin actions
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
2. Call `mcp_discover_tools` or `scripts/ticket.py discover-mcp` to map available Atlassian MCP tools.
3. Call `kanban_ensure_board` to create the Hermes project and Kanban board.
4. Call `kanban_create_item` with the Jira issue JSON; the item is placed by Jira status and enriched with comments/dependencies.
5. Assign a Hermes profile with `kanban_assign_profile`.
6. Resolve the local repo with `repository_resolve`.
7. Start a git branch with `work_start_branch`.
8. Read `CLAUDE.md`, `AGENT.md`, and `README.md` in the target repo before editing.
9. Commit with `work_commit`.
10. After user review, use `pr_plan` and then the configured provider MCP or approved CLI.

## MCP Discovery

```bash
python3 scripts/ticket.py discover-mcp \
  --tools "mcp_atlassian_searchJiraIssues,mcp_atlassian_getJiraIssue"
```

Discovery maps available Atlassian MCP tools to canonical actions such as `search_issues`, `get_issue`, `create_issue`, `assign_issue`, and `transition_issue`.

## Cron Sync

Use `scripts/jira_cron_sync.py` with Jira issue JSON exported through official Atlassian MCP:

```bash
python3 scripts/jira_cron_sync.py \
  --issues-json /tmp/hermes-assigned-jira.json \
  --assignee you@example.com \
  --profile default
```

See `config/cron.example` for a crontab template.

Cron sync keeps the Hermes Kanban board aligned with assigned Jira work. Imported issues move to the configured column for their Jira status, carry recent Jira comments into item context, and include dependency metadata from Jira issue links. If an issue has active blockers and the board defines a `Blocked` column, the item moves there until the blockers clear.

Configure Jira-status-to-column behavior in `HERMES_REPO_MAP` with `statusColumns`; see `config/repositories.example.json` and `docs/configuration.md`.

## Validation

```bash
python3 -m unittest discover -s tests
env PYTHONPYCACHEPREFIX=/private/tmp/atlassian-hermes-pycache \
  python3 -m py_compile __init__.py schemas.py tools.py workflow.py scripts/ticket.py scripts/jira_cron_sync.py
```
