"""Hermes tool handlers for Atlassian Hermes Connector."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict

try:
    from . import workflow
except ImportError:  # Allows local CLI/tests to import tools.py as a top-level module.
    import workflow


def _wrap(handler: Callable[[Dict[str, Any]], Dict[str, Any]], args: Dict[str, Any]) -> str:
    try:
        return workflow.json_result(handler(args or {}))
    except workflow.WorkflowError as exc:
        return workflow.json_result({"success": False, "error": str(exc)})
    except Exception as exc:  # pragma: no cover - Hermes plugin boundary
        return workflow.json_result({"success": False, "error": f"Unexpected error: {exc}"})


def kanban_ensure_board(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.ensure_board, args)


def kanban_create_item(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.create_kanban_item, args)


def kanban_assign_profile(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.assign_profile, args)


def kanban_sync_assigned(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.sync_assigned_kanban_items, args)


def mcp_discover_tools(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.discover_atlassian_mcp_tools, args)


def repository_resolve(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.resolve_repository_payload, args)


def work_start_branch(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.start_branch, args)


def work_commit(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.commit_work, args)


def pr_plan(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return _wrap(workflow.pr_plan, args)


def provider_status(args: Dict[str, Any], **kwargs) -> str:
    del args, kwargs
    return json.dumps(workflow.provider_status(), indent=2, sort_keys=True)
