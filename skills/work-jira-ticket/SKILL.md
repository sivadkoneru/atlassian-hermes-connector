---
name: work-jira-ticket
description: Execute a Jira issue as local development work with Hermes. Use to fetch Jira data through Atlassian MCP, assign a Hermes Kanban profile, resolve the target repository, create a git branch, read CLAUDE.md or AGENT.md and README.md, implement requested changes, run checks, and prepare a commit.
version: 1.0.0
author: Siva Koneru
license: MIT
metadata:
  hermes:
    tags: [Jira, Git, Repository, Automation]
    requires_tools: [mcp_discover_tools, repository_resolve, work_start_branch]
---

# Work Jira Ticket

Use this skill to turn one Jira issue into a local implementation branch.

## Start

1. Call `mcp_discover_tools` if the exact Atlassian MCP tool names are unknown.
2. Fetch the ticket through the configured official Atlassian MCP server.
3. Resolve the repository using `HERMES_REPO_MAP`, `HERMES_DEVELOPMENT_ROOT`, Jira labels like `repo:<name>`, or `HERMES_JIRA_REPO_FIELD`.
4. Create or update the Hermes Kanban item with `kanban_create_item`.
5. Start the branch with:

```bash
python3 scripts/ticket.py start --jira-key <ISSUE_KEY> --confirm-branch
```

Use `--repo <name-or-path>` if Jira does not identify the repository clearly.

## Implementation Rules

Before editing the target repository, read these files when present:

1. `CLAUDE.md`
2. `AGENT.md`
3. `README.md`

Then inspect the relevant code and tests. Keep the change scoped to the Jira request, follow existing repository patterns, and avoid unrelated cleanup.

## Commit

After implementation and checks, commit with:

```bash
python3 scripts/ticket.py commit --repo-path <REPO> --jira-key <ISSUE_KEY> --stage-all --confirm-commit
```

Use a custom `--message` only when the default `<ISSUE_KEY>: <summary>` title is not clear enough.

## Handoff

Report the issue key, Hermes profile, repository, branch, commit SHA, changed files, and checks run. Do not create a pull request until the user reviews and approves the work.
