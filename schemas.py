"""Hermes tool schemas for Atlassian Hermes Connector."""

KANBAN_ENSURE_BOARD = {
    "name": "kanban_ensure_board",
    "description": (
        "Create or verify the single configured Hermes project and Kanban board. "
        "Use before creating Jira-backed Hermes Kanban items."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_key": {"type": "string", "description": "Project key; defaults to HERMES_PROJECT_KEY."},
            "project_name": {"type": "string", "description": "Human readable project name."},
            "board_name": {"type": "string", "description": "Board name; defaults to HERMES_KANBAN_BOARD."},
            "columns": {"type": "array", "items": {"type": "string"}},
        },
    },
}

KANBAN_CREATE_ITEM = {
    "name": "kanban_create_item",
    "description": (
        "Create a Hermes Kanban item from Jira MCP data. Accepts Jira issue JSON, "
        "explicit repo tags, description text, and automation mode."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "jira_issue": {"type": "object", "description": "Issue payload fetched from Atlassian Jira MCP."},
            "jira_key": {"type": "string"},
            "jira_url": {"type": "string"},
            "summary": {"type": "string"},
            "description": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "repo": {"type": "string", "description": "Explicit repository override."},
            "profile": {"type": "string", "description": "Hermes profile to assign."},
            "automation_mode": {"type": "string", "enum": ["manual", "semi", "auto"]},
        },
    },
}

KANBAN_ASSIGN_PROFILE = {
    "name": "kanban_assign_profile",
    "description": "Assign a Hermes Kanban item to a configured Hermes profile.",
    "parameters": {
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "jira_key": {"type": "string"},
            "profile": {"type": "string"},
            "profile_data": {"type": "object"},
        },
        "required": ["profile"],
    },
}

KANBAN_SYNC_ASSIGNED = {
    "name": "kanban_sync_assigned",
    "description": (
        "Create or update Hermes Kanban items from Jira issues fetched through "
        "official Atlassian MCP, importing only issues assigned to the configured "
        "username, email, display name, or account id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issues": {"type": "array", "items": {"type": "object"}},
            "assignee": {"type": "string", "description": "Username, email, display name, or account id."},
            "profile": {"type": "string", "description": "Hermes profile assigned to imported items."},
            "automation_mode": {"type": "string", "enum": ["manual", "semi", "auto"]},
        },
        "required": ["issues"],
    },
}

REPOSITORY_RESOLVE = {
    "name": "repository_resolve",
    "description": (
        "Resolve the target local repository from all supported options: explicit "
        "override, tagged repo label, Jira description, custom field, component, "
        "project mapping, alias, and development-root discovery."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "jira_issue": {"type": "object"},
            "item_id": {"type": "string"},
            "jira_key": {"type": "string"},
            "repo": {"type": "string"},
            "description": {"type": "string"},
        },
    },
}

WORK_START_BRANCH = {
    "name": "work_start_branch",
    "description": (
        "Create or check out a git branch for a Hermes Kanban item after applying "
        "the configured automation/manual confirmation policy."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "jira_issue": {"type": "object"},
            "item_id": {"type": "string"},
            "jira_key": {"type": "string"},
            "repo": {"type": "string"},
            "branch": {"type": "string"},
            "branch_prefix": {"type": "string"},
            "automation_mode": {"type": "string", "enum": ["manual", "semi", "auto"]},
            "confirm_branch": {"type": "boolean"},
            "allow_dirty": {"type": "boolean"},
        },
    },
}

WORK_COMMIT = {
    "name": "work_commit",
    "description": "Commit local git changes for a Hermes/Jira item using git.",
    "parameters": {
        "type": "object",
        "properties": {
            "repo_path": {"type": "string"},
            "jira_key": {"type": "string"},
            "summary": {"type": "string"},
            "message": {"type": "string"},
            "stage_all": {"type": "boolean"},
            "automation_mode": {"type": "string", "enum": ["manual", "semi", "auto"]},
            "confirm_commit": {"type": "boolean"},
        },
        "required": ["repo_path", "jira_key"],
    },
}

PR_PLAN = {
    "name": "pr_plan",
    "description": (
        "Prepare a reviewed PR plan. Uses git for branch state and push guidance, "
        "then requires GitHub/Bitbucket MCP or gh/bb CLI before creation can proceed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo_path": {"type": "string"},
            "jira_key": {"type": "string"},
            "summary": {"type": "string"},
            "provider": {"type": "string", "enum": ["auto", "github", "bitbucket"]},
            "base": {"type": "string"},
            "reviewed": {"type": "boolean"},
            "push": {"type": "boolean"},
        },
        "required": ["repo_path", "jira_key"],
    },
}

PROVIDER_STATUS = {
    "name": "provider_status",
    "description": (
        "Check local Hermes connector status, including Atlassian MCP configuration "
        "expectations and GitHub/Bitbucket PR provider readiness."
    ),
    "parameters": {"type": "object", "properties": {}},
}
