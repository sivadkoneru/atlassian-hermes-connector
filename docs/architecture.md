# Architecture

## Goals

Atlassian Hermes Connector coordinates four systems without hiding their boundaries:

- Jira is the source of truth for ticket content through official Atlassian MCP.
- Hermes Kanban is the local operational board for the agent.
- Git is the source of truth for local branch and commit state.
- GitHub or Bitbucket MCP/CLI owns pull request creation.

## Components

```text
Official Atlassian MCP
        |
        v
Hermes Agent ---- atlassian-hermes-connector plugin
        |             |
        |             +-- Hermes Kanban JSON state
        |             +-- Repository resolver
        |             +-- Git branch/commit workflow
        |             +-- PR readiness gate
        |
        +-- GitHub MCP / Bitbucket MCP / approved CLI fallback
```

## Plugin Files

- `plugin.yaml`: Hermes plugin manifest.
- `__init__.py`: registers tools, slash command, hook, and bundled skills.
- `schemas.py`: tool schemas shown to the model.
- `tools.py`: Hermes handler boundary; returns JSON strings and catches failures.
- `workflow.py`: deterministic Kanban, repository, git, and PR planning logic.
- `scripts/jira_cron_sync.py`: cron-safe importer for Jira issue JSON produced by official Atlassian MCP.
- `skills/`: opt-in Hermes skills loaded via the plugin namespace.
- `scripts/ticket.py`: local CLI helper for testing and manual operation.

## MCP Discovery

`mcp_discover_tools` maps whatever official Atlassian MCP tool names Hermes exposes into stable canonical actions used by the rest of the plugin. This avoids hardcoding exact Atlassian tool names in workflow code.

The discovery layer accepts direct tool names, Hermes catalog JSON, or `HERMES_ATLASSIAN_MCP_TOOLS`, then reports:

- mapped action to MCP tool name
- confidence
- matched token pattern
- call hints for common Jira arguments
- missing actions

## Data Model

Kanban state is stored at `HERMES_KANBAN_STATE_PATH`:

```json
{
  "project": {"key": "HER", "name": "Hermes Delivery"},
  "board": {"name": "Hermes Delivery Board", "columns": ["Backlog", "Ready", "In Progress", "Review", "Done"]},
  "profiles": {},
  "items": []
}
```

Each item stores Jira key, Jira URL, summary, description, repository hints, assigned Hermes profile, status, and automation mode.

Status is synchronized from Jira through the configurable `statusColumns` map in `HERMES_REPO_MAP`. Sync updates move existing Hermes Kanban items when Jira transitions, instead of treating the Hermes board as a separate status source.

Items can also store imported Jira comments and dependency context from Jira issue links. Active blockers mark the item blocked; when the board declares a `Blocked` column, blocked work is placed there until dependencies clear. Generated work packets include the same comments and dependency context for implementation.

## Branch Naming

Branch naming is deterministic and Jira-aware:

```text
<branch-type>/<jira-key-lower>-<summary-slug>
```

The branch type is selected from explicit branch labels, Jira issue type, labels, status, and priority. The default map supports `hotfix`, `bugfix`, `feature`, `docs`, `test`, and `chore`.

## Design Considerations

- Do not duplicate Jira auth. The plugin relies on official Atlassian MCP and Hermes OAuth handling.
- Keep local state inspectable. JSON is easy to review, commit-ignore, and migrate.
- Make repository resolution explainable. The tool returns the selected repo, score, reason, and all supported matching options. Each repository is scored on its strongest hint, and hint sources are banded far enough apart that Jira project/label/component signals can only break ties between equally strong hints — never override a stated one. An explicit `repo` argument therefore always wins, and two repositories that match equally well raise an ambiguity error rather than a silent guess.
- Separate git from PR providers. `git` handles branch, commit, and push; PR creation requires provider MCP or explicit CLI fallback.
- Require human review before PR creation by default.
- Sync only assigned Jira work. The cron importer refuses to create/update items unless the Jira assignee matches the configured username/email/account id.
- Keep Jira context attached to local work. Comments, status, and blockers travel from Jira into Hermes Kanban and generated work packets.
- Keep secrets out of the repository. `.env.example` contains placeholders only.
