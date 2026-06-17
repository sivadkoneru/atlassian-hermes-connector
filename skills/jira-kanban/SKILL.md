---
name: jira-kanban
description: Create, inspect, search, assign, and transition Jira-backed Hermes Kanban work items through Atlassian MCP and the Atlassian Hermes Connector plugin.
version: 1.0.0
author: Siva Koneru
license: MIT
metadata:
  hermes:
    tags: [Jira, Kanban, MCP, Workflow]
    requires_tools: [kanban_create_item]
---

# Jira Kanban

Use the official Atlassian Jira/Rovo MCP server for live Jira work. Use this plugin for the Hermes-side Kanban board, profile assignment, and local workflow state.

## Workflow

1. Confirm the Atlassian MCP server is configured in Hermes as `atlassian` or the name in `HERMES_ATLASSIAN_MCP_SERVER`.
2. Use Atlassian MCP tools to create or fetch the Jira issue for the single configured project and Kanban board.
3. Call `kanban_ensure_board` to create or verify the local Hermes project and Kanban board.
4. Call `kanban_create_item` with the Jira issue JSON, summary, description, labels, and automation mode.
5. Assign a Hermes profile with `kanban_assign_profile`.

## Repository Hints

When creating an item that should drive local implementation, include at least one repository hint:

- label: `repo:<repository-name>`
- component that maps to a repository in `HERMES_REPO_MAP`
- custom field named by `HERMES_JIRA_REPO_FIELD`
- description text like `Repository: <repository-name>`

## Safety

Do not invent Jira account ids, board ids, project keys, transition names, repository names, or Hermes profiles. If a value is missing, inspect Jira through MCP first or ask the user for the missing configuration.
