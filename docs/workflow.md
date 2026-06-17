# Hermes Jira Workflow

## Ticket Intake

Use Jira MCP tools for live Jira operations:

1. `jira_get_myself` to confirm the active Jira profile.
2. `jira_create_kanban_item` to create a task in the configured Jira project.
3. `jira_assign_profile` to assign the item to the configured profile.
4. `jira_get_issue` to load the details before local work begins.

## Local Work

Use `scripts/ticket.py start <ISSUE_KEY>` to resolve the repository and create a branch.

The command creates `.hermes/work/<ISSUE_KEY>.md` in the target repo. Treat this file as the handoff between Jira and implementation.

Before editing code, read these files in the target repository when present:

1. `CLAUDE.md`
2. `AGENT.md`
3. `README.md`

Repository instructions override plugin defaults unless they conflict with user instructions or safety requirements.

## Review Gate

Do not create a pull request immediately after committing. Show the user:

- issue key and summary
- repository and branch
- tests or checks run
- commit SHA
- PR title and description preview

Only run `scripts/ticket.py pr <ISSUE_KEY> --push --create` after the user approves.
