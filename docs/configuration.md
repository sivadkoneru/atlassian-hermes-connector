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
