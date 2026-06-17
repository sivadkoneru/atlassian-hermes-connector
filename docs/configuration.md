# Configuration

## Hermes Plugin

Install the repository as a user plugin:

```bash
mkdir -p ~/.hermes/plugins
ln -s "$(pwd)" ~/.hermes/plugins/atlassian-hermes-connector
hermes plugins enable atlassian-hermes-connector
```

Project-local plugins can also live under `./.hermes/plugins/`, but Hermes disables project-local plugins by default. Enable them only for trusted repositories.

## Official Atlassian MCP

This plugin does not ship a Jira REST client or custom Jira MCP server. Configure the official Atlassian Jira/Rovo MCP server in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  atlassian:
    url: "${ATLASSIAN_MCP_URL}"
    auth: oauth
    oauth:
      client_id: "${ATLASSIAN_MCP_OAUTH_CLIENT_ID}"
      client_secret: "${ATLASSIAN_MCP_OAUTH_CLIENT_SECRET}"
```

Use the endpoint and OAuth client details supplied by Atlassian/Rovo admin setup. Then run:

```bash
hermes mcp login atlassian
hermes mcp configure atlassian
```

Hermes registers MCP tools as `mcp_<server>_<tool>`, so an Atlassian server named `atlassian` exposes tools with names like `mcp_atlassian_*`.

## Atlassian MCP Tool Discovery

The plugin can auto-map available Atlassian MCP tools to canonical Jira actions:

```bash
python3 scripts/ticket.py discover-mcp \
  --tools "mcp_atlassian_searchJiraIssues,mcp_atlassian_getJiraIssue,mcp_atlassian_createJiraIssue"
```

Supported canonical actions:

- `search_issues`
- `get_issue`
- `create_issue`
- `assign_issue`
- `transition_issue`
- `get_myself`

Discovery accepts:

- a comma-separated list through `HERMES_ATLASSIAN_MCP_TOOLS`
- a JSON catalog path through `HERMES_MCP_TOOL_CATALOG`
- direct tool input via `mcp_discover_tools`
- CLI input via `scripts/ticket.py discover-mcp`

`provider_status` includes the discovery result when tool names or a catalog are configured.

## One Project And Board

The plugin intentionally manages one Hermes project and one Hermes Kanban board:

```bash
HERMES_PROJECT_KEY=HER
HERMES_PROJECT_NAME="Hermes Delivery"
HERMES_KANBAN_BOARD="Hermes Delivery Board"
HERMES_KANBAN_STATE_PATH=~/.hermes/atlassian-hermes-connector/kanban.json
```

Create or verify the board:

```bash
python3 scripts/ticket.py board
```

## Repository Map

Point `HERMES_REPO_MAP` to a private JSON file. The public example shows all supported matching options:

```json
{
  "roots": ["~/Development"],
  "profiles": {
    "default": {
      "displayName": "Primary Developer",
      "jiraAccountId": "account-id",
      "deployment": {
        "strategy": "manual-approval",
        "environments": ["local", "staging"]
      }
    }
  },
  "repositories": [
    {
      "name": "service-name",
      "path": "~/Development/service-name",
      "provider": "github",
      "aliases": ["service"],
      "jira": {
        "projects": ["HER"],
        "components": ["backend"],
        "labels": ["repo:service-name"]
      }
    }
  ]
}
```

## Status Columns

`statusColumns` defines which Jira status names belong in each Hermes Kanban column. Keep the board columns and status mapping in the same `HERMES_REPO_MAP` file so cron sync can move items without hardcoded workflow rules:

```json
{
  "board": {
    "columns": ["Backlog", "Ready", "In Progress", "Blocked", "Review", "Done"]
  },
  "statusColumns": {
    "Backlog": ["Backlog", "To Do", "Open"],
    "Ready": ["Selected for Development", "Ready"],
    "In Progress": ["In Progress", "Implementing"],
    "Blocked": ["Blocked", "Waiting", "On Hold"],
    "Review": ["Code Review", "In Review", "Review"],
    "Done": ["Done", "Resolved", "Closed"]
  }
}
```

When a Jira issue has active blockers from issue links, the item is marked blocked. If the board includes a `Blocked` column, blocked items move there; otherwise they remain in the column selected from Jira status and keep the blocked flag in item context.

## Repository Resolution Order

Repository selection considers all options:

1. explicit `repo` argument
2. Jira labels like `repo:<repository-name>`; this is the default and highest-signal path
3. Jira description text like `Repository: <repository-name>`
4. Jira custom field named by `HERMES_JIRA_REPO_FIELD`
5. configured Jira component mappings
6. configured Jira project mappings
7. configured repository aliases
8. git repositories discovered under `HERMES_DEVELOPMENT_ROOT`

## Branch Naming

Branch names are derived from Jira issue data:

- `Bug`, `Defect`, or labels like `bug` -> `bugfix/<issue-key>-<summary>`
- `Story`, `Feature`, `Epic`, or labels like `enhancement` -> `feature/<issue-key>-<summary>`
- `Incident`, `Hotfix`, critical priority, or labels like `hotfix` -> `hotfix/<issue-key>-<summary>`
- documentation labels/types -> `docs/<issue-key>-<summary>`
- test/QA labels/types -> `test/<issue-key>-<summary>`
- task/maintenance fallback -> `chore/<issue-key>-<summary>`

A Jira label can override the type:

```text
branch:docs
branch-type:bugfix
type:feature
```

Customize mappings in `HERMES_REPO_MAP` with `branchTypes`, or force one prefix with:

```bash
HERMES_BRANCH_FORCE_PREFIX=feature/
```

## Automation Modes

Set `HERMES_AUTOMATION_MODE` or pass `automation_mode` to tools:

- `manual`: every branch, commit, and PR step requires explicit confirmation
- `semi`: planning and Kanban updates are automatic, local git mutations still require confirmation
- `auto`: branch and commit actions can proceed when tool arguments are complete; PR still requires user review unless `HERMES_REQUIRE_REVIEW_BEFORE_PR=false`

## Provider MCP Or CLI

The plugin uses `git` for branch, commit, and push planning. Pull request creation is not possible with `git` alone, so configure one of:

- GitHub MCP: `HERMES_GITHUB_MCP_TOOL=mcp_github_create_pull_request`
- Bitbucket MCP: `HERMES_BITBUCKET_MCP_TOOL=mcp_bitbucket_create_pull_request`
- approved CLI fallback: authenticated `gh` or `bb`

If none is configured, `pr_plan` blocks instead of pretending it can create a PR.

## Assigned Ticket Cron Sync

Set the assignee that cron is allowed to import:

```bash
HERMES_JIRA_ASSIGNEE=you@example.com
```

The sync accepts Jira issue JSON produced by the official Atlassian MCP server and filters locally by assignee email, username, display name, account id, or key:

```bash
python3 scripts/jira_cron_sync.py \
  --issues-json /tmp/hermes-assigned-jira.json \
  --assignee "$HERMES_JIRA_ASSIGNEE" \
  --profile default
```

Install a crontab from `config/cron.example` only after replacing `HERMES_PLUGIN_DIR`, `HERMES_JIRA_ASSIGNEE`, and the Hermes MCP prompt/command for your environment.

During sync, imported Jira comments and dependency/blocker links are stored with the Hermes Kanban item and included in generated work packets. This gives the assigned Hermes profile the latest ticket discussion and unblock criteria before branch work starts.
