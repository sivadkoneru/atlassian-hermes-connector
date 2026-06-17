#!/usr/bin/env python3
"""CLI helper for the Atlassian Hermes Connector plugin."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import workflow


def load_issue_arg(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    path = Path(raw).expanduser()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(raw)


def print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def command_board(args: argparse.Namespace) -> Dict[str, Any]:
    return workflow.ensure_board(
        {
            "project_key": args.project_key,
            "project_name": args.project_name,
            "board_name": args.board_name,
        }
    )


def command_item(args: argparse.Namespace) -> Dict[str, Any]:
    return workflow.create_kanban_item(
        {
            "jira_issue": load_issue_arg(args.issue_json),
            "jira_key": args.jira_key,
            "jira_url": args.jira_url,
            "summary": args.summary,
            "description": args.description,
            "labels": workflow.split_csv(args.labels),
            "repo": args.repo,
            "profile": args.profile,
            "automation_mode": args.automation_mode,
        }
    )


def command_assign(args: argparse.Namespace) -> Dict[str, Any]:
    profile_data = json.loads(args.profile_data) if args.profile_data else {}
    return workflow.assign_profile(
        {
            "item_id": args.item_id,
            "jira_key": args.jira_key,
            "profile": args.profile,
            "profile_data": profile_data,
        }
    )


def command_resolve(args: argparse.Namespace) -> Dict[str, Any]:
    return workflow.resolve_repository_payload(
        {
            "jira_issue": load_issue_arg(args.issue_json),
            "item_id": args.item_id,
            "jira_key": args.jira_key,
            "repo": args.repo,
            "description": args.description,
        }
    )


def command_start(args: argparse.Namespace) -> Dict[str, Any]:
    return workflow.start_branch(
        {
            "jira_issue": load_issue_arg(args.issue_json),
            "item_id": args.item_id,
            "jira_key": args.jira_key,
            "repo": args.repo,
            "branch": args.branch,
            "branch_prefix": args.branch_prefix,
            "automation_mode": args.automation_mode,
            "confirm_branch": args.confirm_branch,
            "allow_dirty": args.allow_dirty,
            "profile": args.profile,
        }
    )


def command_commit(args: argparse.Namespace) -> Dict[str, Any]:
    return workflow.commit_work(
        {
            "repo_path": args.repo_path,
            "jira_key": args.jira_key,
            "summary": args.summary,
            "message": args.message,
            "stage_all": args.stage_all,
            "automation_mode": args.automation_mode,
            "confirm_commit": args.confirm_commit,
        }
    )


def command_pr(args: argparse.Namespace) -> Dict[str, Any]:
    return workflow.pr_plan(
        {
            "repo_path": args.repo_path,
            "jira_key": args.jira_key,
            "summary": args.summary,
            "provider": args.provider,
            "base": args.base,
            "reviewed": args.reviewed,
            "push": args.push,
        }
    )


def command_status(_args: argparse.Namespace) -> Dict[str, Any]:
    return workflow.provider_status()


def command_sync(args: argparse.Namespace) -> Dict[str, Any]:
    issues = load_issue_arg(args.issues_json)
    return workflow.sync_assigned_kanban_items(
        {
            "issues": issues,
            "assignee": args.assignee,
            "profile": args.profile,
            "automation_mode": args.automation_mode,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atlassian Hermes Connector workflow helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    board = subparsers.add_parser("board", help="Create or verify the Hermes project and Kanban board.")
    board.add_argument("--project-key")
    board.add_argument("--project-name")
    board.add_argument("--board-name")
    board.set_defaults(func=command_board)

    item = subparsers.add_parser("item", help="Create a Hermes Kanban item from Jira MCP data.")
    item.add_argument("--issue-json", help="Jira issue JSON string or path fetched from Atlassian MCP.")
    item.add_argument("--jira-key")
    item.add_argument("--jira-url")
    item.add_argument("--summary")
    item.add_argument("--description", default="")
    item.add_argument("--labels", default="")
    item.add_argument("--repo")
    item.add_argument("--profile")
    item.add_argument("--automation-mode", choices=["manual", "semi", "auto"])
    item.set_defaults(func=command_item)

    assign = subparsers.add_parser("assign", help="Assign a Hermes Kanban item to a Hermes profile.")
    assign.add_argument("--item-id")
    assign.add_argument("--jira-key")
    assign.add_argument("--profile", required=True)
    assign.add_argument("--profile-data", help="JSON object with Jira/profile/deployment metadata.")
    assign.set_defaults(func=command_assign)

    resolve = subparsers.add_parser("resolve", help="Resolve the local repository for an item or issue.")
    resolve.add_argument("--issue-json")
    resolve.add_argument("--item-id")
    resolve.add_argument("--jira-key")
    resolve.add_argument("--repo")
    resolve.add_argument("--description")
    resolve.set_defaults(func=command_resolve)

    start = subparsers.add_parser("start", help="Create or checkout a git branch for a Hermes item.")
    start.add_argument("--issue-json")
    start.add_argument("--item-id")
    start.add_argument("--jira-key")
    start.add_argument("--repo")
    start.add_argument("--branch")
    start.add_argument("--branch-prefix")
    start.add_argument("--profile")
    start.add_argument("--automation-mode", choices=["manual", "semi", "auto"])
    start.add_argument("--confirm-branch", action="store_true")
    start.add_argument("--allow-dirty", action="store_true")
    start.set_defaults(func=command_start)

    commit = subparsers.add_parser("commit", help="Commit staged changes for a Jira/Hermes item.")
    commit.add_argument("--repo-path", required=True)
    commit.add_argument("--jira-key", required=True)
    commit.add_argument("--summary")
    commit.add_argument("--message")
    commit.add_argument("--stage-all", action="store_true")
    commit.add_argument("--automation-mode", choices=["manual", "semi", "auto"])
    commit.add_argument("--confirm-commit", action="store_true")
    commit.set_defaults(func=command_commit)

    pr = subparsers.add_parser("pr-plan", help="Prepare a reviewed PR plan.")
    pr.add_argument("--repo-path", required=True)
    pr.add_argument("--jira-key", required=True)
    pr.add_argument("--summary")
    pr.add_argument("--provider", choices=["auto", "github", "bitbucket"], default="auto")
    pr.add_argument("--base")
    pr.add_argument("--reviewed", action="store_true")
    pr.add_argument("--push", action="store_true")
    pr.set_defaults(func=command_pr)

    status = subparsers.add_parser("status", help="Show provider readiness.")
    status.set_defaults(func=command_status)

    sync = subparsers.add_parser("sync-assigned", help="Sync assigned Jira issues into Hermes Kanban state.")
    sync.add_argument("--issues-json", required=True, help="Jira issues JSON string or path from Atlassian MCP.")
    sync.add_argument("--assignee", help="Username, email, display name, or account id to import.")
    sync.add_argument("--profile", help="Hermes profile assigned to imported items.")
    sync.add_argument("--automation-mode", choices=["manual", "semi", "auto"])
    sync.set_defaults(func=command_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print_json(args.func(args))
        return 0
    except workflow.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
