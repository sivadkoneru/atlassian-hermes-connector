# Hermes Jira Workflow

## Ticket Intake

Use official Atlassian MCP tools for live Jira operations:

1. confirm the Atlassian MCP server is connected
2. create or fetch the Jira issue in the one configured project
3. include a repository hint, preferably `repo:<repository-name>` as a label
4. include `Repository: <repository-name>` in the description when labels are unavailable

Then create the Hermes-side item:

```bash
python3 scripts/ticket.py item \
  --jira-key HER-123 \
  --summary "Add deployment gate" \
  --description "Repository: my-buddy-service" \
  --labels "repo:my-buddy-service" \
  --profile default
```

## Hermes Board And Profile

The Hermes Kanban board is local plugin state. It mirrors the work queue that Hermes should operate on, while Jira remains the source of truth for ticket content.

Assign the Hermes profile that owns implementation, deployment hand-off, and follow-up process:

```bash
python3 scripts/ticket.py assign \
  --jira-key HER-123 \
  --profile default \
  --profile-data '{"deployment":"staging hand-off required"}'
```

## Local Work

Resolve the repository:

```bash
python3 scripts/ticket.py resolve --jira-key HER-123
```

Start a branch. In manual mode, pass the confirmation flag:

```bash
python3 scripts/ticket.py start --jira-key HER-123 --confirm-branch
```

Branch prefixes come from Jira issue type, priority, and labels. Examples:

- bug issue: `bugfix/her-123-fix-login`
- story or feature: `feature/her-124-add-dashboard`
- hotfix or critical incident: `hotfix/her-125-restore-webhook`
- docs label: `docs/her-126-update-runbook`

The command creates `.hermes/work/<ISSUE_KEY>.md` in the target repo. Treat this file as the handoff between Jira, Hermes Kanban, and implementation.

Before editing code, read these files in the target repository when present:

1. `CLAUDE.md`
2. `AGENT.md`
3. `README.md`

Repository instructions override plugin defaults unless they conflict with user instructions or safety requirements.

## Commit

After implementation and checks:

```bash
python3 scripts/ticket.py commit \
  --repo-path ~/Development/my-buddy-service \
  --jira-key HER-123 \
  --stage-all \
  --confirm-commit
```

## Review Gate

Do not create a pull request immediately after committing. Show the user:

- issue key and summary
- Hermes profile
- repository and branch
- tests or checks run
- commit SHA
- PR title and description preview

After approval:

```bash
python3 scripts/ticket.py pr-plan \
  --repo-path ~/Development/my-buddy-service \
  --jira-key HER-123 \
  --summary "Add deployment gate" \
  --reviewed
```

If a provider MCP tool is configured, use it. If not, ask the user whether to use `gh` or `bb`. If neither is available, stop.

## Scheduled Jira Sync

Use `scripts/jira_cron_sync.py` from cron to update/create Hermes Kanban tasks from Jira issue JSON. The script imports only issues assigned to `HERMES_JIRA_ASSIGNEE` or the `--assignee` value:

```bash
python3 scripts/jira_cron_sync.py \
  --issues-json /tmp/hermes-assigned-jira.json \
  --assignee you@example.com \
  --profile default
```

See `config/cron.example` for a crontab template. Keep the Jira query/export step backed by the official Atlassian MCP server configured in Hermes.
