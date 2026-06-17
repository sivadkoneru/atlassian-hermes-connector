#!/usr/bin/env python3
"""Cron-friendly Jira-to-Hermes Kanban sync.

This script expects Jira issue JSON exported through the official Atlassian MCP
path. It intentionally filters by assignee locally before creating/updating
Hermes Kanban items.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import workflow


def load_json(path: str | None, use_stdin: bool) -> Any:
    if use_stdin:
        return json.loads(sys.stdin.read())
    if not path:
        raise workflow.WorkflowError("--issues-json or --stdin is required.")
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync assigned Jira issues into Hermes Kanban state.")
    parser.add_argument("--issues-json", help="Path to Jira issues JSON produced through official Atlassian MCP.")
    parser.add_argument("--stdin", action="store_true", help="Read Jira issues JSON from stdin.")
    parser.add_argument("--assignee", help="Username, email, display name, or account id to import.")
    parser.add_argument("--profile", help="Hermes profile assigned to imported items.")
    parser.add_argument("--automation-mode", choices=["manual", "semi", "auto"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = workflow.sync_assigned_kanban_items(
            {
                "issues": load_json(args.issues_json, args.stdin),
                "assignee": args.assignee,
                "profile": args.profile,
                "automation_mode": args.automation_mode,
            }
        )
    except (json.JSONDecodeError, OSError, workflow.WorkflowError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
