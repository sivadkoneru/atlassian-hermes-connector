"""Atlassian Hermes Connector plugin registration."""

from __future__ import annotations

import logging
from pathlib import Path

from . import schemas, tools

LOGGER = logging.getLogger(__name__)
TOOLSET = "atlassian_hermes_connector"
TOOL_NAMES = {
    schemas.KANBAN_ENSURE_BOARD["name"],
    schemas.KANBAN_CREATE_ITEM["name"],
    schemas.KANBAN_ASSIGN_PROFILE["name"],
    schemas.KANBAN_SYNC_ASSIGNED["name"],
    schemas.REPOSITORY_RESOLVE["name"],
    schemas.WORK_START_BRANCH["name"],
    schemas.WORK_COMMIT["name"],
    schemas.PR_PLAN["name"],
    schemas.PROVIDER_STATUS["name"],
}


def _register_skills(ctx) -> None:
    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.is_dir():
        return
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, skill_md)


def _on_post_tool_call(tool_name, args, result, task_id=None, **kwargs) -> None:
    del args, result, kwargs
    if str(tool_name) in TOOL_NAMES:
        LOGGER.debug("Atlassian Hermes tool called: %s task=%s", tool_name, task_id)


def _status_command(_raw_args: str) -> str:
    return tools.provider_status({})


def register(ctx) -> None:
    """Wire Hermes tool schemas, handlers, skills, and lightweight commands."""

    registrations = [
        (schemas.KANBAN_ENSURE_BOARD, tools.kanban_ensure_board),
        (schemas.KANBAN_CREATE_ITEM, tools.kanban_create_item),
        (schemas.KANBAN_ASSIGN_PROFILE, tools.kanban_assign_profile),
        (schemas.KANBAN_SYNC_ASSIGNED, tools.kanban_sync_assigned),
        (schemas.REPOSITORY_RESOLVE, tools.repository_resolve),
        (schemas.WORK_START_BRANCH, tools.work_start_branch),
        (schemas.WORK_COMMIT, tools.work_commit),
        (schemas.PR_PLAN, tools.pr_plan),
        (schemas.PROVIDER_STATUS, tools.provider_status),
    ]
    for schema, handler in registrations:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            description=schema["description"],
        )

    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_command(
        "kanban-status",
        handler=_status_command,
        description="Show Atlassian Hermes Connector provider and board status.",
    )
    _register_skills(ctx)
