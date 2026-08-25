"""Core workflow logic for the Atlassian Hermes Connector plugin."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


DEFAULT_COLUMNS = ["Backlog", "Ready", "In Progress", "Review", "Done"]
DEFAULT_STATUS_COLUMN_MAP = {
    "Open": "Backlog",
    "To Do": "Backlog",
    "Todo": "Backlog",
    "Backlog": "Backlog",
    "Selected for Development": "Ready",
    "Ready": "Ready",
    "In Progress": "In Progress",
    "Doing": "In Progress",
    "Code Review": "Review",
    "In Review": "Review",
    "Review": "Review",
    "Done": "Done",
    "Closed": "Done",
    "Resolved": "Done",
}
DEFAULT_BLOCKED_COLUMN = "Blocked"
# Directory this plugin writes work packets to, relative to the target repo.
WORK_PACKET_DIR = ".hermes"
# Repository match weights, banded so hint precedence is provable rather than accidental:
#   max config bonus (15) < smallest gap between sources (20)
#       -> config signals never outrank a hint, and no cross-source tie can occur
#   best substring + bonus (115) < weakest exact (200)
#       -> a fuzzy hint never hides an exact one
#   gap between exact tiers (100) > max bonus
#       -> an explicit repo argument always outranks a conflicting Jira label
HINT_SOURCE_EXACT = {
    "explicit": 600,
    "jira_label": 500,
    "jira_description": 400,
    "jira_custom_field": 300,
    "description": 200,
}
HINT_SOURCE_SUBSTRING = {
    "explicit": 100,
    "jira_label": 80,
    "jira_description": 60,
    "jira_custom_field": 40,
    "description": 20,
}
ATLASSIAN_MCP_ACTIONS = {
    "search_issues": {
        "patterns": [
            ["jira", "search", "issue"],
            ["search", "jira", "issues"],
            ["search", "issues"],
            ["issue", "search"],
            ["jql"],
        ],
        "call_hint": {
            "jql": "assignee = currentUser() AND project = ${HERMES_PROJECT_KEY} ORDER BY priority DESC, updated DESC"
        },
    },
    "get_issue": {
        "patterns": [
            ["jira", "get", "issue"],
            ["get", "jira", "issue"],
            ["get", "issue"],
            ["read", "issue"],
            ["fetch", "issue"],
        ],
        "call_hint": {"issue_key": "HER-123"},
    },
    "create_issue": {
        "patterns": [
            ["jira", "create", "issue"],
            ["create", "jira", "issue"],
            ["create", "issue"],
        ],
        "call_hint": {"project_key": "${HERMES_PROJECT_KEY}", "summary": "..."},
    },
    "assign_issue": {
        "patterns": [
            ["jira", "assign", "issue"],
            ["assign", "jira", "issue"],
            ["assign", "issue"],
        ],
        "call_hint": {"issue_key": "HER-123", "assignee": "${HERMES_JIRA_ASSIGNEE}"},
    },
    "transition_issue": {
        "patterns": [
            ["jira", "transition", "issue"],
            ["transition", "jira", "issue"],
            ["transition", "issue"],
            ["move", "issue"],
            ["update", "status"],
        ],
        "call_hint": {"issue_key": "HER-123", "transition": "In Progress"},
    },
    "get_myself": {
        "patterns": [
            ["jira", "myself"],
            ["myself"],
            ["current", "user"],
            ["get", "user", "current"],
        ],
        "call_hint": {},
    },
}
DEFAULT_BRANCH_TYPES = {
    "hotfix": {
        "issue_types": ["Incident", "Hotfix"],
        "labels": ["hotfix", "urgent", "p0", "p1"],
        "statuses": [],
        "priorities": ["Highest", "Critical", "Blocker"],
    },
    "bugfix": {
        "issue_types": ["Bug", "Defect"],
        "labels": ["bug", "bugfix", "defect"],
        "statuses": [],
        "priorities": [],
    },
    "feature": {
        "issue_types": ["Story", "Feature", "New Feature", "Epic"],
        "labels": ["feature", "enhancement"],
        "statuses": [],
        "priorities": [],
    },
    "docs": {
        "issue_types": ["Documentation"],
        "labels": ["docs", "documentation"],
        "statuses": [],
        "priorities": [],
    },
    "test": {
        "issue_types": ["Test", "QA"],
        "labels": ["test", "tests", "qa"],
        "statuses": [],
        "priorities": [],
    },
    "chore": {
        "issue_types": ["Task", "Sub-task", "Subtask", "Maintenance"],
        "labels": ["chore", "maintenance", "refactor"],
        "statuses": [],
        "priorities": [],
    },
}


class WorkflowError(Exception):
    """Raised for expected user-correctable workflow failures."""


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or value == "" or value.startswith("${"):
        return default
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str, max_length: int = 64) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return (slug or "work")[:max_length].strip("-")


def slug_contains_segment(haystack: str, needle: str) -> bool:
    """Whole-segment containment: `api` matches `api-gateway` but not `rapid`."""

    if not needle or not haystack:
        return False
    return re.search(rf"(?:^|-){re.escape(needle)}(?:-|$)", haystack) is not None


def split_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def expand_path(raw_path: str | Path) -> Path:
    return Path(str(raw_path)).expanduser().resolve()


def required_arg(args: Dict[str, Any], name: str) -> str:
    value = args.get(name)
    if value is None or not str(value).strip():
        raise WorkflowError(f"{name} is required.")
    return str(value).strip()


def safe_file_component(value: str, label: str) -> str:
    """Guard a path segment built from Jira-supplied text against traversal."""

    cleaned = str(value).strip()
    if not cleaned or cleaned in {".", ".."} or set(cleaned) & {"/", "\\", "\0"}:
        raise WorkflowError(f"{label} cannot be used as a file name: {value!r}")
    return cleaned


def json_result(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def run_git(repo: Path, args: List[str], *, check: bool = True) -> str:
    # Always capture: these handlers return JSON, so git must never write to stdout.
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        detail = stderr or stdout or f"git {' '.join(args)} failed"
        raise WorkflowError(detail)
    return result.stdout.strip() if result.stdout else ""


def load_json_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Config file is not valid JSON: {path}: {exc}") from exc


def load_config() -> Dict[str, Any]:
    raw_path = env("HERMES_REPO_MAP")
    if raw_path:
        return load_json_file(expand_path(raw_path))
    return {
        "project": {
            "key": env("HERMES_PROJECT_KEY", "HER"),
            "name": env("HERMES_PROJECT_NAME", "Hermes Project"),
        },
        "board": {
            "name": env("HERMES_KANBAN_BOARD", "Hermes Kanban"),
            "columns": DEFAULT_COLUMNS,
        },
        "roots": [env("HERMES_DEVELOPMENT_ROOT", "~/Development")],
        "profiles": {},
        "repositories": [],
    }


def load_optional_json(path: Optional[str]) -> Any:
    if not path:
        return None
    candidate = expand_path(path)
    if not candidate.exists():
        raise WorkflowError(f"Tool catalog file not found: {candidate}")
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Tool catalog is not valid JSON: {candidate}: {exc}") from exc


def state_path() -> Path:
    return expand_path(
        env("HERMES_KANBAN_STATE_PATH", "~/.hermes/atlassian-hermes-connector/kanban.json")
    )


def initial_state(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = config or load_config()
    project = config.get("project") or {}
    board = config.get("board") or {}
    project_key = env("HERMES_PROJECT_KEY", project.get("key", "HER")) or "HER"
    project_name = env("HERMES_PROJECT_NAME", project.get("name", "Hermes Project"))
    board_name = env("HERMES_KANBAN_BOARD", board.get("name", "Hermes Kanban"))
    columns = board.get("columns") or DEFAULT_COLUMNS
    return {
        "version": 1,
        "project": {"key": project_key, "name": project_name},
        "board": {"name": board_name, "columns": columns},
        "profiles": config.get("profiles") or {},
        "items": [],
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or state_path()
    if not path.exists():
        return initial_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Hermes Kanban state is not valid JSON: {path}: {exc}") from exc
    state.setdefault("items", [])
    state.setdefault("profiles", {})
    state.setdefault("project", initial_state()["project"])
    state.setdefault("board", initial_state()["board"])
    return state


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    # Write through a temp file so an interrupted run cannot leave truncated JSON.
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return state


def ensure_board(args: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state()
    state["project"] = {
        "key": args.get("project_key") or state.get("project", {}).get("key") or env("HERMES_PROJECT_KEY", "HER"),
        "name": args.get("project_name")
        or state.get("project", {}).get("name")
        or env("HERMES_PROJECT_NAME", "Hermes Project"),
    }
    state["board"] = {
        "name": args.get("board_name")
        or state.get("board", {}).get("name")
        or env("HERMES_KANBAN_BOARD", "Hermes Kanban"),
        "columns": args.get("columns") or state.get("board", {}).get("columns") or DEFAULT_COLUMNS,
    }
    save_state(state)
    return {
        "success": True,
        "project": state["project"],
        "board": state["board"],
        "state_path": str(state_path()),
    }


def issue_fields(issue: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not issue:
        return {}
    return issue.get("fields") or {}


def issue_summary(issue: Optional[Dict[str, Any]], fallback: str = "Jira work item") -> str:
    if not issue:
        return fallback
    return str(issue_fields(issue).get("summary") or issue.get("summary") or issue.get("key") or fallback)


def issue_key(issue: Optional[Dict[str, Any]], fallback: Optional[str] = None) -> Optional[str]:
    if not issue:
        return fallback
    return str(issue.get("key") or issue.get("jira_key") or fallback or "")


def issue_url(issue: Optional[Dict[str, Any]], fallback: Optional[str] = None) -> Optional[str]:
    if not issue:
        return fallback
    return issue.get("browseUrl") or issue.get("url") or issue.get("jira_url") or fallback


def issue_project_key(issue: Dict[str, Any]) -> Optional[str]:
    project = issue_fields(issue).get("project")
    if isinstance(project, dict):
        key = project.get("key")
        if key:
            return str(key)
    key = issue_key(issue)
    if key and "-" in key:
        return key.split("-", 1)[0]
    return None


def normalize_values(values: Any) -> Set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    result: Set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("name") or value.get("value") or value.get("key")
        if value is not None:
            text = str(value).strip()
            if text:
                result.add(text.casefold())
                result.add(slugify(text).casefold())
    return result


def issue_labels(issue: Dict[str, Any], extra_labels: Optional[Iterable[str]] = None) -> Set[str]:
    values: List[Any] = list(issue_fields(issue).get("labels") or [])
    if extra_labels:
        values.extend(extra_labels)
    return normalize_values(values)


def issue_components(issue: Dict[str, Any]) -> Set[str]:
    return normalize_values(issue_fields(issue).get("components"))


def issue_type_name(issue: Optional[Dict[str, Any]]) -> str:
    if not issue:
        return ""
    issue_type = issue_fields(issue).get("issuetype") or issue.get("issuetype")
    if isinstance(issue_type, dict):
        return str(issue_type.get("name") or issue_type.get("value") or "")
    return str(issue_type or "")


def issue_status_name(issue: Optional[Dict[str, Any]]) -> str:
    if not issue:
        return ""
    status = issue_fields(issue).get("status") or issue.get("status")
    if isinstance(status, dict):
        return str(status.get("name") or status.get("value") or "")
    return str(status or "")


def issue_priority_name(issue: Optional[Dict[str, Any]]) -> str:
    if not issue:
        return ""
    priority = issue_fields(issue).get("priority") or issue.get("priority")
    if isinstance(priority, dict):
        return str(priority.get("name") or priority.get("value") or "")
    return str(priority or "")


def branch_type_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, List[str]]]:
    configured = (config or load_config()).get("branchTypes") or {}
    branch_types: Dict[str, Dict[str, List[str]]] = {
        name: {key: list(values) for key, values in values_by_key.items()}
        for name, values_by_key in DEFAULT_BRANCH_TYPES.items()
    }
    if isinstance(configured, dict):
        for raw_name, raw_rules in configured.items():
            if not isinstance(raw_rules, dict):
                continue
            name = slugify(str(raw_name), 32)
            merged = branch_types.setdefault(
                name,
                {"issue_types": [], "labels": [], "statuses": [], "priorities": []},
            )
            for key in ("issue_types", "labels", "statuses", "priorities"):
                values = raw_rules.get(key) or raw_rules.get(key.replace("_", ""))
                if isinstance(values, str):
                    values = [values]
                if isinstance(values, list):
                    merged[key].extend(str(value) for value in values)
    return branch_types


def explicit_branch_type_from_labels(issue: Optional[Dict[str, Any]]) -> Optional[str]:
    if not issue:
        return None
    for label in issue_fields(issue).get("labels") or []:
        text = str(label)
        for prefix in ("branch:", "branch-type:", "type:"):
            if text.casefold().startswith(prefix):
                return slugify(text.split(":", 1)[1], 32)
    return None


def branch_type_for_issue(issue: Optional[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> str:
    explicit = explicit_branch_type_from_labels(issue)
    if explicit:
        return explicit
    labels = issue_labels(issue or {})
    issue_type = normalize_values([issue_type_name(issue)])
    status = normalize_values([issue_status_name(issue)])
    priority = normalize_values([issue_priority_name(issue)])
    for branch_type, rules in branch_type_config(config).items():
        if labels & normalize_values(rules.get("labels")):
            return branch_type
        if issue_type & normalize_values(rules.get("issue_types")):
            return branch_type
        if status & normalize_values(rules.get("statuses")):
            return branch_type
        if priority & normalize_values(rules.get("priorities")):
            return branch_type
    return env("HERMES_BRANCH_DEFAULT_TYPE", "chore") or "chore"


def branch_prefix_for_issue(
    issue: Optional[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    explicit_prefix: Optional[str] = None,
) -> str:
    prefix = explicit_prefix
    if not prefix:
        forced = env("HERMES_BRANCH_FORCE_PREFIX")
        prefix = forced if forced else f"{branch_type_for_issue(issue, config)}/"
    return prefix if prefix.endswith("/") else f"{prefix}/"


def branch_name_for_issue(
    issue: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    explicit_prefix: Optional[str] = None,
) -> str:
    key = issue_key(issue, "JIRA")
    return f"{branch_prefix_for_issue(issue, config, explicit_prefix)}{key.lower()}-{slugify(issue_summary(issue), 42)}"


def flatten_adf(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        chunks: List[str] = []
        text = value.get("text")
        if isinstance(text, str):
            chunks.append(text)
        for child in value.get("content") or []:
            chunks.append(flatten_adf(child))
        return "\n".join(chunk for chunk in chunks if chunk)
    if isinstance(value, list):
        return "\n".join(flatten_adf(item) for item in value)
    return str(value)


def issue_description_text(issue: Optional[Dict[str, Any]], fallback: str = "") -> str:
    if not issue:
        return fallback
    rendered = issue.get("renderedFields") or {}
    if rendered.get("description"):
        return str(rendered["description"])
    fields = issue_fields(issue)
    return flatten_adf(fields.get("description")) or str(issue.get("description") or fallback or "")


def actor_name(actor: Any) -> str:
    if isinstance(actor, dict):
        for key in ("displayName", "emailAddress", "email", "name", "accountId", "key"):
            if actor.get(key):
                return str(actor[key])
    if actor:
        return str(actor)
    return "Unknown"


def raw_comment_collection(issue: Optional[Dict[str, Any]]) -> List[Any]:
    if not issue:
        return []
    fields = issue_fields(issue)
    candidates = [
        issue.get("comments"),
        issue.get("comment"),
        fields.get("comment"),
        fields.get("comments"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            comments = candidate.get("comments") or candidate.get("values") or candidate.get("results")
            if isinstance(comments, list):
                return comments
        if isinstance(candidate, list):
            return candidate
    return []


def jira_comments(issue: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    comments: List[Dict[str, str]] = []
    for raw in raw_comment_collection(issue):
        if isinstance(raw, str):
            body = raw.strip()
            if body:
                comments.append({"author": "Unknown", "body": body})
            continue
        if not isinstance(raw, dict):
            continue
        body = (
            flatten_adf(raw.get("renderedBody"))
            or flatten_adf(raw.get("body"))
            or flatten_adf(raw.get("text"))
        ).strip()
        if not body:
            continue
        comment: Dict[str, str] = {
            "author": actor_name(raw.get("author") or raw.get("updateAuthor")),
            "body": body,
        }
        for key in ("id", "created", "updated"):
            if raw.get(key):
                comment[key] = str(raw[key])
        comments.append(comment)
    return comments


def issue_status_category_name(issue: Optional[Dict[str, Any]]) -> str:
    status = issue_fields(issue).get("status") if issue else None
    if not isinstance(status, dict):
        status = issue.get("status") if issue else None
    if isinstance(status, dict):
        category = status.get("statusCategory") or status.get("category")
        if isinstance(category, dict):
            return str(category.get("name") or category.get("key") or "")
        if category:
            return str(category)
    return ""


def issue_is_done(issue: Optional[Dict[str, Any]]) -> bool:
    values = normalize_values([issue_status_name(issue), issue_status_category_name(issue)])
    return bool(values & normalize_values(["Done", "Closed", "Resolved"]))


def linked_issue_summary(raw_issue: Any) -> Dict[str, Any]:
    if isinstance(raw_issue, str):
        return {"key": raw_issue, "summary": raw_issue, "status": "", "done": False}
    if not isinstance(raw_issue, dict):
        return {"key": "", "summary": str(raw_issue), "status": "", "done": False}
    key = issue_key(raw_issue, "")
    status = issue_status_name(raw_issue)
    payload: Dict[str, Any] = {
        "key": key,
        "summary": issue_summary(raw_issue, key or "Linked Jira issue"),
        "status": status,
        "done": issue_is_done(raw_issue),
    }
    url = issue_url(raw_issue)
    if url:
        payload["url"] = url
    return payload


def append_linked_issue(target: List[Dict[str, Any]], raw_issue: Any) -> None:
    payload = linked_issue_summary(raw_issue)
    key = payload.get("key")
    if key and any(item.get("key") == key for item in target):
        return
    target.append(payload)


def relation_means_blocked_by(relation: str, type_name: str = "") -> bool:
    value = f"{relation} {type_name}".casefold()
    return any(phrase in value for phrase in ("blocked by", "depends on", "dependent on", "requires"))


def relation_means_blocks(relation: str, type_name: str = "") -> bool:
    value = f"{relation} {type_name}".casefold()
    return "blocks" in value or "depended on by" in value or "required by" in value


def raw_issue_links(issue: Optional[Dict[str, Any]]) -> List[Any]:
    if not issue:
        return []
    fields = issue_fields(issue)
    links: List[Any] = []
    for key in ("issuelinks", "issueLinks", "links"):
        value = fields.get(key) if key in fields else issue.get(key)
        if isinstance(value, list):
            links.extend(value)
    return links


def raw_dependency_list(issue: Optional[Dict[str, Any]], keys: Iterable[str]) -> List[Any]:
    if not issue:
        return []
    fields = issue_fields(issue)
    values: List[Any] = []
    for key in keys:
        candidate = issue.get(key)
        if candidate is None:
            candidate = fields.get(key)
        if isinstance(candidate, dict) and "issues" in candidate:
            candidate = candidate["issues"]
        if isinstance(candidate, list):
            values.extend(candidate)
        elif candidate:
            values.append(candidate)
    return values


def normalize_existing_dependencies(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"blocked_by": [], "blocks": [], "active_blockers": [], "has_blockers": False}
    blocked_by = [linked_issue_summary(item) for item in raw.get("blocked_by") or raw.get("blockedBy") or []]
    blocks = [linked_issue_summary(item) for item in raw.get("blocks") or []]
    active_blockers = [item for item in blocked_by if not item.get("done")]
    if raw.get("active_blockers"):
        active_blockers = [linked_issue_summary(item) for item in raw["active_blockers"]]
    return {
        "blocked_by": blocked_by,
        "blocks": blocks,
        "active_blockers": active_blockers,
        "has_blockers": bool(active_blockers or raw.get("has_blockers")),
    }


def jira_dependencies(issue: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    existing = issue.get("dependencies") if issue else None
    if isinstance(existing, dict) and any(key in existing for key in ("blocked_by", "blockedBy", "blocks")):
        return normalize_existing_dependencies(existing)

    blocked_by: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    for link in raw_issue_links(issue):
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        type_name = str(link_type.get("name") or "") if isinstance(link_type, dict) else str(link_type)
        inward_relation = str(link_type.get("inward") or "") if isinstance(link_type, dict) else ""
        outward_relation = str(link_type.get("outward") or "") if isinstance(link_type, dict) else ""

        inward_issue = link.get("inwardIssue") or link.get("inward")
        if inward_issue:
            if relation_means_blocked_by(inward_relation, type_name) or type_name.casefold() == "blocks":
                append_linked_issue(blocked_by, inward_issue)
            elif relation_means_blocks(inward_relation, type_name):
                append_linked_issue(blocks, inward_issue)

        outward_issue = link.get("outwardIssue") or link.get("outward")
        if outward_issue:
            if relation_means_blocked_by(outward_relation, type_name):
                append_linked_issue(blocked_by, outward_issue)
            elif relation_means_blocks(outward_relation, type_name) or type_name.casefold() == "blocks":
                append_linked_issue(blocks, outward_issue)

    for raw in raw_dependency_list(issue, ("blockedBy", "blocked_by", "dependsOn", "dependencies")):
        append_linked_issue(blocked_by, raw)
    for raw in raw_dependency_list(issue, ("blocks", "blocking")):
        append_linked_issue(blocks, raw)

    active_blockers = [item for item in blocked_by if not item.get("done")]
    return {
        "blocked_by": blocked_by,
        "blocks": blocks,
        "active_blockers": active_blockers,
        "has_blockers": bool(active_blockers),
    }


def normalized_status_key(status: str) -> List[str]:
    return [status.strip().casefold(), slugify(status).casefold()]


def board_column_named(columns: List[str], name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    for column in columns:
        if column.casefold() == str(name).casefold():
            return column
    return None


def status_column_map(config: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    config = config or load_config()
    board = config.get("board") or {}
    raw_configured = (
        config.get("statusColumns")
        or board.get("statusColumns")
        or config.get("status_columns")
        or board.get("status_columns")
        or {}
    )
    mapping: Dict[str, str] = {}

    def add(status: Any, column: Any) -> None:
        if not status or not column:
            return
        for key in normalized_status_key(str(status)):
            mapping[key] = str(column)

    for status, column in DEFAULT_STATUS_COLUMN_MAP.items():
        add(status, column)
    if isinstance(raw_configured, dict):
        for raw_status, raw_column in raw_configured.items():
            if isinstance(raw_column, list):
                for status in raw_column:
                    add(status, raw_status)
            else:
                add(raw_status, raw_column)
    return mapping


def kanban_column_for_issue(
    issue: Optional[Dict[str, Any]],
    columns: List[str],
    config: Optional[Dict[str, Any]] = None,
    fallback: Optional[str] = None,
) -> str:
    config = config or load_config()
    fallback_column = board_column_named(columns, fallback) or columns[0]
    status = issue_status_name(issue)
    mapping = status_column_map(config)
    mapped_column = None
    for key in normalized_status_key(status):
        mapped_column = mapping.get(key)
        if mapped_column:
            break
    column = board_column_named(columns, mapped_column) or fallback_column
    blocked_column = board_column_named(
        columns,
        (config.get("board") or {}).get("blockedColumn")
        or config.get("blockedColumn")
        or DEFAULT_BLOCKED_COLUMN,
    )
    if blocked_column and not issue_is_done(issue) and jira_dependencies(issue).get("has_blockers"):
        return blocked_column
    return column


def custom_field_value(issue: Dict[str, Any], configured_name: Optional[str]) -> Any:
    if not configured_name:
        return None
    fields = issue_fields(issue)
    if configured_name in fields:
        return fields[configured_name]
    names = issue.get("names") or {}
    if isinstance(names, dict):
        for field_id, display_name in names.items():
            if str(display_name).casefold() == configured_name.casefold():
                return fields.get(field_id)
    return None


def value_to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("name", "value", "key", "slug"):
            if value.get(key):
                return str(value[key]).strip()
    if isinstance(value, list) and value:
        return value_to_text(value[0])
    return str(value).strip() or None


def repo_hints_from_text(text: str) -> List[str]:
    patterns = [
        r"\b(?:repo|repository|hermes-repo)\s*[:=]\s*([A-Za-z0-9_.-]+)",
        r"\bgithub\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
        r"\bbitbucket\.org[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    ]
    hints: List[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            hint = match.group(1).strip().strip("`'\".,)")
            if "/" in hint:
                hint = hint.rsplit("/", 1)[-1]
            if hint and hint not in hints:
                hints.append(hint)
    return hints


def repo_hints_from_issue(
    issue: Optional[Dict[str, Any]],
    explicit_repo: Optional[str] = None,
    description: Optional[str] = None,
    labels: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    hints: List[Dict[str, str]] = []

    def add(source: str, value: Optional[str]) -> None:
        if not value:
            return
        clean = value.strip()
        if clean and all(item["value"].casefold() != clean.casefold() for item in hints):
            hints.append({"source": source, "value": clean})

    add("explicit", explicit_repo)
    if issue:
        for label in list(issue_fields(issue).get("labels") or []) + list(labels or []):
            text = str(label)
            for prefix in ("repo:", "repository:", "hermes-repo:"):
                if text.casefold().startswith(prefix):
                    add("jira_label", text.split(":", 1)[1])
        for hint in repo_hints_from_text(issue_description_text(issue)):
            add("jira_description", hint)
        repo_field = env("HERMES_JIRA_REPO_FIELD")
        add("jira_custom_field", value_to_text(custom_field_value(issue, repo_field)))
    if description:
        for hint in repo_hints_from_text(description):
            add("description", hint)
    return hints


def repo_hint_from_issue(issue: Dict[str, Any], explicit_repo: Optional[str] = None) -> Optional[str]:
    hints = repo_hints_from_issue(issue, explicit_repo)
    return hints[0]["value"] if hints else None


@dataclass
class RepoMatch:
    name: str
    path: Path
    provider: Optional[str] = None
    score: int = 0
    reason: str = ""
    hint_source: Optional[str] = None


def configured_repositories(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    repos = config.get("repositories") or []
    if not isinstance(repos, list):
        raise WorkflowError("repositories must be an array in HERMES_REPO_MAP.")
    return [repo for repo in repos if isinstance(repo, dict)]


def discover_git_repositories(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    repos: List[Dict[str, Any]] = []
    roots = config.get("roots") or [env("HERMES_DEVELOPMENT_ROOT", "~/Development")]
    for root in roots:
        root_path = expand_path(root)
        try:
            children = sorted(root_path.iterdir())
        except OSError:  # missing or unreadable development root
            continue
        for child in children:
            if child.is_dir() and (child / ".git").exists():
                repos.append({"name": child.name, "path": str(child)})
    return repos


def score_repo(repo: Dict[str, Any], issue: Dict[str, Any], hints: List[Dict[str, str]]) -> Optional[RepoMatch]:
    path_value = repo.get("path")
    if not path_value:
        return None
    path = expand_path(path_value)
    name = str(repo.get("name") or path.name)
    raw_aliases = repo.get("aliases") or []
    if isinstance(raw_aliases, str):
        raw_aliases = [raw_aliases]
    alias_values = [name, path.name, *raw_aliases]
    aliases = normalize_values(alias_values)
    alias_slugs = {slugify(value) for value in alias_values if value}
    score = 0
    reasons: List[str] = []
    source: Optional[str] = None

    # Keep the best hint rather than the first that matches: a weak substring hit from a
    # high-precedence hint must not hide an exact hit from a lower-precedence one.
    for hint in hints:
        hint_value = hint["value"]
        hint_source = hint["source"]
        # slugify() falls back to "work" for punctuation-only input, which would match
        # any repository whose name contains "work".
        hint_slug = slugify(hint_value) if any(char.isalnum() for char in hint_value) else ""
        if aliases & normalize_values([hint_value]):
            points = HINT_SOURCE_EXACT.get(hint_source, HINT_SOURCE_EXACT["description"])
            reason = f"{hint_source} matched {hint_value}"
        elif any(
            slug_contains_segment(alias_slug, hint_slug) or slug_contains_segment(hint_slug, alias_slug)
            for alias_slug in alias_slugs
        ):
            points = HINT_SOURCE_SUBSTRING.get(hint_source, HINT_SOURCE_SUBSTRING["description"])
            reason = f"{hint_source} partially matched {hint_value}"
        else:
            continue
        if points > score:
            score = points
            source = hint_source
            reasons = [reason]

    jira = repo.get("jira") or {}
    project_key = issue_project_key(issue)
    if project_key and project_key.casefold() in normalize_values(jira.get("projects")):
        score += 8
        reasons.append(f"project matched {project_key}")
    # Flat, not per-overlap: normalize_values stores both the raw and slugified form of a
    # value, so counting overlaps double-counts anything containing punctuation.
    if issue_labels(issue) & normalize_values(jira.get("labels")):
        score += 4
        reasons.append("labels matched")
    if issue_components(issue) & normalize_values(jira.get("components")):
        score += 2
        reasons.append("components matched")
    if slug_contains_segment(slugify(issue_summary(issue)), slugify(name)):
        score += 1
        reasons.append("repository name appeared in summary")

    if score <= 0:
        return None
    return RepoMatch(
        name=name,
        path=path,
        provider=repo.get("provider"),
        score=score,
        reason=", ".join(reasons),
        hint_source=source,
    )


def resolve_repository(
    issue: Dict[str, Any],
    config: Dict[str, Any],
    explicit_repo: Optional[str] = None,
    description: Optional[str] = None,
) -> RepoMatch:
    hints = repo_hints_from_issue(issue, explicit_repo, description)
    repos = configured_repositories(config)
    if not repos:
        repos = discover_git_repositories(config)
    matches = [match for repo in repos if (match := score_repo(repo, issue, hints))]
    matches.sort(key=lambda item: item.score, reverse=True)
    if not matches:
        hint_text = ", ".join(item["value"] for item in hints) or "no hint"
        raise WorkflowError(
            "Could not resolve a repository. Tried hints: "
            f"{hint_text}. Configure HERMES_REPO_MAP, add repo:<name> label, "
            "or mention Repository: <name> in the Jira description."
        )
    if len(matches) > 1 and matches[0].score == matches[1].score:
        names = ", ".join(
            f"{match.name} ({match.score}; {match.hint_source or 'config only'})" for match in matches[:5]
        )
        raise WorkflowError(
            f"Repository match is ambiguous: {names}. Pass repo=<name>, add a "
            "repo:<name> Jira label, or give the repositories distinct aliases."
        )
    selected = matches[0]
    if not selected.path.exists():
        raise WorkflowError(f"Resolved repository path does not exist: {selected.path}")
    if not (selected.path / ".git").exists():
        raise WorkflowError(f"Resolved path is not a git repository: {selected.path}")
    return selected


def automation_mode(args: Dict[str, Any]) -> str:
    mode = args.get("automation_mode") or env("HERMES_AUTOMATION_MODE", "manual")
    if mode not in {"manual", "semi", "auto"}:
        raise WorkflowError("automation_mode must be manual, semi, or auto.")
    return mode


def require_confirmation(args: Dict[str, Any], key: str, action: str) -> Optional[Dict[str, Any]]:
    mode = automation_mode(args)
    if mode == "auto" or args.get(key):
        return None
    return {
        "success": False,
        "requires_confirmation": True,
        "automation_mode": mode,
        "confirmation_key": key,
        "next_action": f"Re-run with {key}=true to {action}.",
    }


def item_id_from_key(jira_key: str) -> str:
    return slugify(jira_key, 80)


def find_item(state: Dict[str, Any], item_id: Optional[str] = None, jira_key: Optional[str] = None) -> Dict[str, Any]:
    for item in state.get("items", []):
        if item_id and item.get("id") == item_id:
            return item
        if jira_key and item.get("jira_key") == jira_key:
            return item
    raise WorkflowError("Hermes Kanban item was not found.")


def build_kanban_item(args: Dict[str, Any], state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Merge one Jira issue into the in-memory board state and return the item.

    Callers own loading and saving state so a batch import writes the file once.
    """

    jira_issue = args.get("jira_issue") or {}
    key = args.get("jira_key") or issue_key(jira_issue)
    if not key:
        raise WorkflowError("jira_key or jira_issue.key is required.")
    summary = args.get("summary") or issue_summary(jira_issue, fallback=key)
    description = args.get("description") or issue_description_text(jira_issue)
    labels = args.get("labels") or issue_fields(jira_issue).get("labels") or []
    hints = repo_hints_from_issue(jira_issue, args.get("repo"), description, labels)
    existing = next(
        (old for old in state.get("items", []) if old.get("id") == item_id_from_key(key)),
        None,
    )
    columns = state["board"]["columns"]
    explicit_status = args.get("status")
    if explicit_status:
        status = board_column_named(columns, explicit_status)
        if not status:
            raise WorkflowError(f"status must match a Hermes Kanban column: {', '.join(columns)}")
    else:
        status = kanban_column_for_issue(
            jira_issue,
            columns,
            config,
            fallback=existing.get("status") if existing else None,
        )
    dependencies = jira_dependencies(jira_issue)
    comments = jira_comments(jira_issue)
    item = {
        "id": item_id_from_key(key),
        "jira_key": key,
        "jira_url": args.get("jira_url") or issue_url(jira_issue),
        "summary": summary,
        "description": description,
        "labels": labels,
        "repo_hints": hints,
        "profile": args.get("profile"),
        "status": status,
        "jira_status": issue_status_name(jira_issue),
        "blocked": bool(dependencies.get("has_blockers")),
        "dependencies": dependencies,
        "comments": comments,
        "automation_mode": automation_mode(args),
        "branch_type": branch_type_for_issue(jira_issue, config),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    if existing:
        item["created_at"] = existing.get("created_at", item["created_at"])
        item["profile"] = item["profile"] or existing.get("profile")
        if existing.get("profile_data") and item["profile"] == existing.get("profile"):
            item["profile_data"] = existing["profile_data"]
        if not item["jira_status"]:
            item["jira_status"] = existing.get("jira_status", "")
        if not comments and existing.get("comments"):
            item["comments"] = existing["comments"]
        if not dependencies.get("blocked_by") and not dependencies.get("blocks") and existing.get("dependencies"):
            item["dependencies"] = normalize_existing_dependencies(existing["dependencies"])
            item["blocked"] = bool(item["dependencies"].get("has_blockers"))
        if not jira_issue and existing.get("branch_type"):
            item["branch_type"] = existing["branch_type"]
    items = [old for old in state.get("items", []) if old.get("id") != item["id"]]
    items.append(item)
    state["items"] = items
    return item


def create_kanban_item(args: Dict[str, Any]) -> Dict[str, Any]:
    config = load_config()
    state = load_state()
    item = build_kanban_item(args, state, config)
    save_state(state)
    return {"success": True, "item": item, "state_path": str(state_path())}


def assignee_values(issue: Dict[str, Any]) -> Set[str]:
    assignee = issue_fields(issue).get("assignee") or issue.get("assignee") or {}
    if not isinstance(assignee, dict):
        return normalize_values([assignee])
    values = []
    for key in ("emailAddress", "email", "name", "displayName", "accountId", "key"):
        if assignee.get(key):
            values.append(assignee[key])
    return normalize_values(values)


def assignee_matches(issue: Dict[str, Any], assignee: str) -> bool:
    if not assignee:
        raise WorkflowError("assignee is required for Jira sync.")
    return bool(assignee_values(issue) & normalize_values([assignee]))


def normalize_issue_collection(raw_issues: Any) -> List[Dict[str, Any]]:
    if isinstance(raw_issues, dict):
        raw_issues = raw_issues.get("issues") or raw_issues.get("data") or [raw_issues]
    if not isinstance(raw_issues, list):
        raise WorkflowError("issues must be a list or an object containing an issues array.")
    return [issue for issue in raw_issues if isinstance(issue, dict)]


def sync_assigned_kanban_items(args: Dict[str, Any]) -> Dict[str, Any]:
    issues = normalize_issue_collection(args.get("issues") or [])
    assignee = args.get("assignee") or env("HERMES_JIRA_ASSIGNEE")
    if not assignee:
        raise WorkflowError("Set HERMES_JIRA_ASSIGNEE or pass assignee.")

    created: List[str] = []
    updated: List[str] = []
    moved: List[Dict[str, str]] = []
    blocked: List[str] = []
    skipped: List[Dict[str, str]] = []
    config = load_config()
    state = load_state()
    existing_keys = {item.get("jira_key") for item in state.get("items", [])}
    existing_statuses = {
        item.get("jira_key"): item.get("status")
        for item in state.get("items", [])
        if item.get("jira_key")
    }

    for issue in issues:
        key = issue_key(issue)
        if not key:
            skipped.append({"key": "", "reason": "missing Jira key"})
            continue
        if not assignee_matches(issue, assignee):
            skipped.append({"key": key, "reason": "assignee did not match"})
            continue
        synced_item = build_kanban_item(
            {
                "jira_issue": issue,
                "profile": args.get("profile") or env("HERMES_PROFILE"),
                "automation_mode": args.get("automation_mode"),
            },
            state,
            config,
        )
        previous_status = existing_statuses.get(key)
        if previous_status and previous_status != synced_item.get("status"):
            moved.append(
                {
                    "key": key,
                    "from": str(previous_status),
                    "to": str(synced_item.get("status")),
                    "jira_status": str(synced_item.get("jira_status") or ""),
                }
            )
        existing_statuses[key] = synced_item.get("status")
        if synced_item.get("blocked"):
            blocked.append(key)
        if key in existing_keys:
            updated.append(key)
        else:
            created.append(key)
            existing_keys.add(key)
        if args.get("profile"):
            apply_profile(state, synced_item, args["profile"], {})
    save_state(state)

    return {
        "success": True,
        "assignee": assignee,
        "created": created,
        "updated": updated,
        "moved": moved,
        "blocked": blocked,
        "skipped": skipped,
        "state_path": str(state_path()),
    }


def apply_profile(
    state: Dict[str, Any],
    item: Dict[str, Any],
    profile_name: str,
    profile_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register the profile on the in-memory state and attach it to one item."""

    profiles = state.setdefault("profiles", {})
    existing_profile = profiles.get(profile_name)
    if not isinstance(existing_profile, dict):
        existing_profile = {}
    existing_profile.update(profile_data or {})
    existing_profile.setdefault("name", profile_name)
    profiles[profile_name] = existing_profile
    item["profile"] = profile_name
    item["profile_data"] = existing_profile
    item["updated_at"] = utc_now()
    return existing_profile


def assign_profile(args: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state()
    profile_name = required_arg(args, "profile")
    item = find_item(state, args.get("item_id"), args.get("jira_key"))
    profile = apply_profile(state, item, profile_name, args.get("profile_data") or {})
    save_state(state)
    return {"success": True, "item": item, "profile": profile}


def issue_from_args_or_item(args: Dict[str, Any]) -> Dict[str, Any]:
    if args.get("jira_issue"):
        return args["jira_issue"]
    state = load_state()
    item = find_item(state, args.get("item_id"), args.get("jira_key"))
    return {
        "key": item.get("jira_key"),
        "summary": item.get("summary"),
        "description": item.get("description"),
        "jira_url": item.get("jira_url"),
        "comments": item.get("comments") or [],
        "dependencies": item.get("dependencies") or {},
        "fields": {
            "summary": item.get("summary"),
            "labels": item.get("labels") or [],
            "description": item.get("description"),
            "status": {"name": item.get("jira_status") or item.get("status") or ""},
        },
        "browseUrl": item.get("jira_url"),
    }


def repository_resolution_options(issue: Dict[str, Any], args: Dict[str, Any]) -> List[Dict[str, str]]:
    options = repo_hints_from_issue(issue, args.get("repo"), args.get("description"))
    options.extend(
        [
            {"source": "jira_component", "value": "Map Jira components in HERMES_REPO_MAP repositories[].jira.components"},
            {"source": "jira_project", "value": "Map Jira project in HERMES_REPO_MAP repositories[].jira.projects"},
            {"source": "repository_alias", "value": "Use repositories[].aliases for alternate names"},
            {"source": "development_root", "value": "Discover git repos under HERMES_DEVELOPMENT_ROOT"},
        ]
    )
    return options


def resolve_repository_payload(args: Dict[str, Any]) -> Dict[str, Any]:
    config = load_config()
    issue = issue_from_args_or_item(args)
    match = resolve_repository(issue, config, args.get("repo"), args.get("description"))
    return {
        "success": True,
        "repository": {
            "name": match.name,
            "path": str(match.path),
            "provider": match.provider,
            "score": match.score,
            "reason": match.reason,
            "hint_source": match.hint_source,
        },
        "resolution_options": repository_resolution_options(issue, args),
    }


def find_repo_guidance(repo: Path) -> List[Path]:
    names = ("CLAUDE.md", "AGENT.md", "README.md")
    return [repo / name for name in names if (repo / name).exists()]


def branch_exists(repo: Path, branch: str) -> bool:
    """Exact local branch lookup; `git branch --list` would treat the name as a glob."""

    return bool(run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], check=False))


def ensure_clean_worktree(repo: Path, allow_dirty: bool) -> None:
    # Work packets this plugin writes to .hermes/ must not count as user changes,
    # otherwise the first start_branch blocks every later one in the same repo.
    status = run_git(repo, ["status", "--porcelain", "--", ".", f":(exclude){WORK_PACKET_DIR}"])
    if status and not allow_dirty:
        raise WorkflowError(
            f"{repo} has uncommitted changes. Commit, stash, or rerun with allow_dirty=true."
        )


def format_linked_issue(item: Dict[str, Any]) -> str:
    key = item.get("key") or "unknown"
    summary = item.get("summary") or key
    status = item.get("status") or "unknown status"
    suffix = " done" if item.get("done") else ""
    return f"{key}: {summary} ({status}{suffix})"


def format_dependency_context(dependencies: Dict[str, Any]) -> str:
    if not isinstance(dependencies, dict):
        return "- No dependencies detected"
    lines: List[str] = []
    active_blockers = dependencies.get("active_blockers") or []
    blocked_by = dependencies.get("blocked_by") or []
    blocks = dependencies.get("blocks") or []
    if active_blockers:
        lines.append("Active blockers:")
        lines.extend(f"- {format_linked_issue(item)}" for item in active_blockers)
    if blocked_by:
        if lines:
            lines.append("")
        lines.append("Blocked by:")
        lines.extend(f"- {format_linked_issue(item)}" for item in blocked_by)
    if blocks:
        if lines:
            lines.append("")
        lines.append("Blocks:")
        lines.extend(f"- {format_linked_issue(item)}" for item in blocks)
    return "\n".join(lines) if lines else "- No dependencies detected"


def format_comment_context(comments: List[Dict[str, str]]) -> str:
    if not comments:
        return "- No Jira comments found"
    lines: List[str] = []
    for comment in comments:
        timestamp = comment.get("updated") or comment.get("created") or "undated"
        author = comment.get("author") or "Unknown"
        lines.append(f"- {timestamp} by {author}:")
        body_lines = (comment.get("body") or "").splitlines() or [""]
        lines.extend(f"  {line}" for line in body_lines)
    return "\n".join(lines)


def create_work_packet(repo: Path, issue: Dict[str, Any], branch: str, profile_name: Optional[str]) -> Path:
    key = issue_key(issue, "JIRA")
    packet_dir = repo / WORK_PACKET_DIR / "work"
    packet_dir.mkdir(parents=True, exist_ok=True)
    packet = packet_dir / f"{safe_file_component(key, 'Jira key')}.md"
    guidance = find_repo_guidance(repo)
    guidance_lines = "\n".join(f"- {path.relative_to(repo)}" for path in guidance) or "- None found"
    dependencies = jira_dependencies(issue)
    comments = jira_comments(issue)
    body = f"""# {key}: {issue_summary(issue)}

Jira: {issue_url(issue, key)}
Branch: {branch}
Hermes profile: {profile_name or env("HERMES_PROFILE", "default")}
Jira status: {issue_status_name(issue) or "Unknown"}

## Dependencies

{format_dependency_context(dependencies)}

## Jira Comments

{format_comment_context(comments)}

## Repository Guidance

Read these files before editing:
{guidance_lines}

## Work Instructions

1. Use the official Atlassian Jira MCP issue details as source of truth.
2. Check dependencies and blockers before changing code.
3. Read Jira comments for clarifications and acceptance notes.
4. Read repository guidance before editing.
5. Inspect the existing implementation and tests.
6. Make the requested change with the smallest reasonable scope.
7. Run relevant checks.
8. Commit only intended changes.
9. Wait for user review before PR creation.
"""
    packet.write_text(body, encoding="utf-8")
    return packet


def start_branch(args: Dict[str, Any]) -> Dict[str, Any]:
    blocked = require_confirmation(args, "confirm_branch", "create or check out the branch")
    if blocked:
        return blocked

    issue = issue_from_args_or_item(args)
    config = load_config()
    match = resolve_repository(issue, config, args.get("repo"))
    repo = match.path
    ensure_clean_worktree(repo, bool(args.get("allow_dirty")))
    branch = args.get("branch") or branch_name_for_issue(issue, config, args.get("branch_prefix"))
    if branch_exists(repo, branch):
        run_git(repo, ["checkout", branch])
    else:
        run_git(repo, ["checkout", "-b", branch])
    packet = create_work_packet(repo, issue, branch, args.get("profile"))
    return {
        "success": True,
        "repository": str(repo),
        "branch": branch,
        "work_packet": str(packet),
        "guidance_files": [str(path) for path in find_repo_guidance(repo)],
    }


def commit_work(args: Dict[str, Any]) -> Dict[str, Any]:
    blocked = require_confirmation(args, "confirm_commit", "commit local changes")
    if blocked:
        return blocked
    repo = expand_path(required_arg(args, "repo_path"))
    jira_key = required_arg(args, "jira_key")
    if args.get("stage_all"):
        run_git(repo, ["add", "-A"])
    staged = run_git(repo, ["diff", "--cached", "--name-only"])
    if not staged:
        raise WorkflowError("No staged changes to commit. Stage files or pass stage_all=true.")
    summary = args.get("summary") or jira_key
    message = args.get("message") or f"{jira_key}: {summary}"
    run_git(repo, ["commit", "-m", message])
    return {
        "success": True,
        "repository": str(repo),
        "commit": run_git(repo, ["rev-parse", "HEAD"]),
        "message": message,
    }


def parse_remote(remote_url: str) -> Dict[str, Optional[str]]:
    # Repository names may contain dots, and remotes may carry a trailing slash.
    patterns = [
        ("github", r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
        ("bitbucket", r"bitbucket\.org[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    ]
    for provider, pattern in patterns:
        match = re.search(pattern, remote_url)
        if match:
            return {"provider": provider, "owner": match.group("owner"), "repo": match.group("repo")}
    return {"provider": None, "owner": None, "repo": None}


def current_branch(repo: Path) -> str:
    branch = run_git(repo, ["branch", "--show-current"], check=False)
    if branch:
        return branch
    symbolic = run_git(repo, ["symbolic-ref", "--short", "HEAD"], check=False)
    if symbolic:
        return symbolic
    raise WorkflowError("Could not determine current git branch. Create or check out a branch first.")


def configured_mcp_tool(provider: str) -> Optional[str]:
    env_name = f"HERMES_{provider.upper()}_MCP_TOOL"
    return env(env_name)


def tokenize_tool_name(name: str) -> Set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    parts = re.split(r"[^A-Za-z0-9]+", spaced)
    tokens = {part.lower() for part in parts if part}
    expanded = set(tokens)
    for token in tokens:
        if token.endswith("issues"):
            expanded.add("issue")
        if token.endswith("s") and len(token) > 3:
            expanded.add(token[:-1])
    return expanded


def tool_name_from_entry(entry: Any) -> Optional[str]:
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        for key in ("name", "tool", "id", "function"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        function = entry.get("function")
        if isinstance(function, dict):
            value = function.get("name")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def tools_from_catalog(catalog: Any) -> List[str]:
    if catalog is None:
        return []
    if isinstance(catalog, str):
        return split_csv(catalog) or [catalog]
    if isinstance(catalog, list):
        return [name for item in catalog if (name := tool_name_from_entry(item))]
    if isinstance(catalog, dict):
        direct_name = tool_name_from_entry(catalog)
        if direct_name:
            return [direct_name]
        for key in ("tools", "data", "items"):
            if key in catalog:
                return tools_from_catalog(catalog[key])
        server_tools: List[str] = []
        for value in catalog.values():
            if isinstance(value, (list, dict)):
                server_tools.extend(tools_from_catalog(value))
        return server_tools
    return []


def configured_atlassian_tool_names(args: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    names.extend(tools_from_catalog(args.get("tools")))
    names.extend(tools_from_catalog(args.get("tool_catalog")))
    names.extend(tools_from_catalog(load_optional_json(args.get("catalog_path"))))
    names.extend(tools_from_catalog(load_optional_json(env("HERMES_MCP_TOOL_CATALOG"))))
    names.extend(split_csv(env("HERMES_ATLASSIAN_MCP_TOOLS")))
    deduped: List[str] = []
    seen: Set[str] = set()
    for name in names:
        if name not in seen:
            deduped.append(name)
            seen.add(name)
    return deduped


def atlassian_candidate_tools(tool_names: List[str], server_name: str) -> List[str]:
    server_prefix = f"mcp_{server_name.lower()}_"
    candidates: List[str] = []
    for name in tool_names:
        lowered = name.lower()
        tokens = tokenize_tool_name(name)
        if lowered.startswith(server_prefix) or "atlassian" in tokens or "jira" in tokens:
            candidates.append(name)
    return candidates


def score_tool_for_action(tool_name: str, action: str, server_name: str) -> Dict[str, Any]:
    tokens = tokenize_tool_name(tool_name)
    lowered = tool_name.lower()
    server_prefix = f"mcp_{server_name.lower()}_"
    best_score = 0
    best_pattern: List[str] = []
    for pattern in ATLASSIAN_MCP_ACTIONS[action]["patterns"]:
        if set(pattern).issubset(tokens):
            score = len(pattern) * 10
            if lowered.startswith(server_prefix):
                score += 8
            if "jira" in tokens:
                score += 5
            if "confluence" in tokens:
                score -= 20
            # Prefer the narrowest tool: getJiraIssue over getJiraIssueRemoteIssueLinks.
            score -= len(tokens - set(pattern) - {"mcp", "jira", server_name.lower()})
            # A genuine pattern match must stay a positive-score candidate even after the
            # penalty, so a sole verbose tool is never dropped by discovery's score > 0 cutoff.
            score = max(score, 1)
            if score > best_score:
                best_score = score
                best_pattern = pattern
    return {"score": best_score, "matched_pattern": best_pattern}


def discover_atlassian_mcp_tools(args: Dict[str, Any]) -> Dict[str, Any]:
    server_name = args.get("server_name") or env("HERMES_ATLASSIAN_MCP_SERVER", "atlassian") or "atlassian"
    tool_names = configured_atlassian_tool_names(args)
    candidates = atlassian_candidate_tools(tool_names, server_name)
    actions: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []

    for action in ATLASSIAN_MCP_ACTIONS:
        scored: List[Dict[str, Any]] = []
        for tool_name in candidates:
            score = score_tool_for_action(tool_name, action, server_name)
            if score["score"] > 0:
                scored.append({"tool": tool_name, **score})
        scored.sort(key=lambda item: item["score"], reverse=True)
        if scored:
            best = scored[0]
            actions[action] = {
                "tool": best["tool"],
                "confidence": "high" if best["score"] >= 30 else "medium",
                "matched_pattern": best["matched_pattern"],
                "call_hint": ATLASSIAN_MCP_ACTIONS[action]["call_hint"],
            }
        else:
            missing.append(action)

    return {
        "success": True,
        "server_name": server_name,
        "available_tools": candidates,
        "actions": actions,
        "missing_actions": missing,
        "configured": bool(actions),
    }


def provider_status(args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    del args
    try:
        discovery = discover_atlassian_mcp_tools({})
    except WorkflowError as exc:
        discovery = {"success": False, "error": str(exc), "actions": {}, "missing_actions": list(ATLASSIAN_MCP_ACTIONS)}
    return {
        "success": True,
        "atlassian_mcp": {
            "server_name": env("HERMES_ATLASSIAN_MCP_SERVER", "atlassian"),
            "url_env": "ATLASSIAN_MCP_URL",
            "configured": bool(env("ATLASSIAN_MCP_URL") or env("HERMES_ATLASSIAN_MCP_SERVER")),
            "note": "Configure the official Atlassian Jira/Rovo MCP server in ~/.hermes/config.yaml.",
            "discovery": discovery,
        },
        "github": {
            "mcp_tool": configured_mcp_tool("github"),
            "gh_cli": shutil.which("gh"),
        },
        "bitbucket": {
            "mcp_tool": configured_mcp_tool("bitbucket"),
            "bb_cli": shutil.which("bb"),
        },
    }


def pr_body(jira_key: str, summary: str, provider: str) -> str:
    return f"""## Summary

Implements {jira_key}: {summary}

## Jira

{jira_key}

## Review Notes

- User review completed before PR creation.
- Provider: {provider}
"""


def pr_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    repo = expand_path(required_arg(args, "repo_path"))
    jira_key = required_arg(args, "jira_key")
    remote_url = run_git(repo, ["remote", "get-url", "origin"])
    parsed = parse_remote(remote_url)
    provider = args.get("provider") or "auto"
    if provider == "auto":
        provider = parsed.get("provider")
    if provider not in {"github", "bitbucket"}:
        raise WorkflowError(
            f"Could not infer provider from remote {remote_url!r}. "
            "Pass provider=github or provider=bitbucket."
        )

    summary = args.get("summary") or jira_key
    branch = current_branch(repo)
    base = args.get("base") or env("HERMES_PR_BASE_BRANCH", "main")
    reviewed = bool(args.get("reviewed"))
    require_review = (env("HERMES_REQUIRE_REVIEW_BEFORE_PR", "true") or "true").lower() != "false"
    if require_review and not reviewed:
        return {
            "success": False,
            "blocked": True,
            "reason": "User review is required before PR creation.",
            "next_action": "Ask the user to review the branch, tests, title, and description.",
        }

    mcp_tool = configured_mcp_tool(provider)
    cli = shutil.which("gh" if provider == "github" else "bb")
    plan = {
        "provider": provider,
        "repository": str(repo),
        "remote": remote_url,
        "branch": branch,
        "base": base,
        "title": f"{jira_key}: {summary}",
        "body": pr_body(jira_key, summary, provider),
        "git_push_command": ["git", "push", "-u", "origin", branch],
    }
    if mcp_tool:
        mcp_arguments = {
            "title": plan["title"],
            "body": plan["body"],
            "source_branch": branch,
            "target_branch": base,
            "base": base,
            "head": branch,
        }
        if parsed.get("owner"):
            mcp_arguments["owner"] = parsed["owner"]
        if parsed.get("repo"):
            mcp_arguments["repository"] = parsed["repo"]
        plan["creation"] = {
            "mode": "mcp",
            "tool": mcp_tool,
            "arguments": mcp_arguments,
            "instruction": "Call the configured provider MCP tool with title, body, source branch, and base branch.",
        }
        return {"success": True, "pull_request": plan}
    if cli:
        if provider == "github":
            cli_command = [
                "gh",
                "pr",
                "create",
                "--title",
                plan["title"],
                "--body",
                plan["body"],
                "--base",
                base,
                "--head",
                branch,
            ]
        else:
            cli_command = [
                "bb",
                "pr",
                "create",
                "--title",
                plan["title"],
                "--description",
                plan["body"],
                "--destination",
                base,
                "--source",
                branch,
            ]
        plan["creation"] = {
            "mode": "cli",
            "command": cli_command,
            "instruction": "Use this CLI only after the user approves using provider CLI instead of MCP.",
        }
        return {"success": True, "pull_request": plan}
    return {
        "success": False,
        "blocked": True,
        "reason": (
            f"No {provider} MCP tool configured and no provider CLI found. "
            f"Set HERMES_{provider.upper()}_MCP_TOOL or install/authenticate "
            f"{'gh' if provider == 'github' else 'bb'}."
        ),
        "pull_request": plan,
    }
