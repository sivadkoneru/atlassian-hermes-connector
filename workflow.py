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
DEFAULT_TOOL_PREFIXES = {
    "github": ["github_create_pull_request", "create_pull_request", "github_pr_create"],
    "bitbucket": ["bitbucket_create_pull_request", "create_pull_request", "bitbucket_pr_create"],
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


def split_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def expand_path(raw_path: str | Path) -> Path:
    return Path(str(raw_path)).expanduser().resolve()


def json_result(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def run_git(repo: Path, args: List[str], *, capture: bool = True, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
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
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
        if not root_path.exists():
            continue
        for child in root_path.iterdir():
            if child.is_dir() and (child / ".git").exists():
                repos.append({"name": child.name, "path": str(child)})
    return repos


def score_repo(repo: Dict[str, Any], issue: Dict[str, Any], hints: List[Dict[str, str]]) -> Optional[RepoMatch]:
    path_value = repo.get("path")
    if not path_value:
        return None
    path = expand_path(path_value)
    name = str(repo.get("name") or path.name)
    aliases = normalize_values([name, path.name, *(repo.get("aliases") or [])])
    score = 0
    reasons: List[str] = []
    source: Optional[str] = None

    for hint in hints:
        hint_value = hint["value"]
        hint_values = normalize_values([hint_value])
        if aliases & hint_values:
            score += 100 if hint["source"] == "jira_label" else 90
            source = hint["source"]
            reasons.append(f"{hint['source']} matched {hint_value}")
            break
        if slugify(hint_value) in slugify(name) or slugify(name) in slugify(hint_value):
            score += 80
            source = hint["source"]
            reasons.append(f"{hint['source']} partially matched {hint_value}")
            break

    jira = repo.get("jira") or {}
    project_key = issue_project_key(issue)
    if project_key and project_key.casefold() in normalize_values(jira.get("projects")):
        score += 20
        reasons.append(f"project matched {project_key}")
    label_overlap = issue_labels(issue) & normalize_values(jira.get("labels"))
    if label_overlap:
        score += 15 * len(label_overlap)
        reasons.append("labels matched")
    component_overlap = issue_components(issue) & normalize_values(jira.get("components"))
    if component_overlap:
        score += 15 * len(component_overlap)
        reasons.append("components matched")
    if slugify(name) in slugify(issue_summary(issue)):
        score += 3
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
        names = ", ".join(f"{match.name} ({match.score})" for match in matches[:5])
        raise WorkflowError(f"Repository match is ambiguous: {names}")
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


def create_kanban_item(args: Dict[str, Any]) -> Dict[str, Any]:
    ensure_board({})
    state = load_state()
    jira_issue = args.get("jira_issue") or {}
    key = args.get("jira_key") or issue_key(jira_issue)
    if not key:
        raise WorkflowError("jira_key or jira_issue.key is required.")
    summary = args.get("summary") or issue_summary(jira_issue, fallback=key)
    description = args.get("description") or issue_description_text(jira_issue)
    labels = args.get("labels") or issue_fields(jira_issue).get("labels") or []
    hints = repo_hints_from_issue(jira_issue, args.get("repo"), description, labels)
    item = {
        "id": item_id_from_key(key),
        "jira_key": key,
        "jira_url": args.get("jira_url") or issue_url(jira_issue),
        "summary": summary,
        "description": description,
        "labels": labels,
        "repo_hints": hints,
        "profile": args.get("profile"),
        "status": state["board"]["columns"][0],
        "automation_mode": automation_mode(args),
        "branch_type": branch_type_for_issue(jira_issue),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    existing = next((old for old in state.get("items", []) if old.get("id") == item["id"]), None)
    if existing:
        item["created_at"] = existing.get("created_at", item["created_at"])
        item["status"] = existing.get("status") or item["status"]
        item["profile"] = item["profile"] or existing.get("profile")
    items = [old for old in state.get("items", []) if old.get("id") != item["id"]]
    items.append(item)
    state["items"] = items
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
    skipped: List[Dict[str, str]] = []
    existing_keys = {item.get("jira_key") for item in load_state().get("items", [])}

    for issue in issues:
        key = issue_key(issue)
        if not key:
            skipped.append({"key": "", "reason": "missing Jira key"})
            continue
        if not assignee_matches(issue, assignee):
            skipped.append({"key": key, "reason": "assignee did not match"})
            continue
        create_kanban_item(
            {
                "jira_issue": issue,
                "profile": args.get("profile") or env("HERMES_PROFILE", "default"),
                "automation_mode": args.get("automation_mode"),
            }
        )
        if key in existing_keys:
            updated.append(key)
        else:
            created.append(key)
            existing_keys.add(key)
        if args.get("profile"):
            assign_profile({"jira_key": key, "profile": args["profile"]})

    return {
        "success": True,
        "assignee": assignee,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "state_path": str(state_path()),
    }


def assign_profile(args: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state()
    profile_name = args.get("profile")
    if not profile_name:
        raise WorkflowError("profile is required.")
    item = find_item(state, args.get("item_id"), args.get("jira_key"))
    profile_data = args.get("profile_data") or {}
    profiles = state.setdefault("profiles", {})
    existing_profile = profiles.get(profile_name, {})
    if not isinstance(existing_profile, dict):
        existing_profile = {}
    existing_profile.update(profile_data)
    existing_profile.setdefault("name", profile_name)
    profiles[profile_name] = existing_profile
    item["profile"] = profile_name
    item["profile_data"] = existing_profile
    item["updated_at"] = utc_now()
    save_state(state)
    return {"success": True, "item": item, "profile": existing_profile}


def issue_from_args_or_item(args: Dict[str, Any]) -> Dict[str, Any]:
    if args.get("jira_issue"):
        return args["jira_issue"]
    state = load_state()
    item = find_item(state, args.get("item_id"), args.get("jira_key"))
    return {
        "key": item.get("jira_key"),
        "summary": item.get("summary"),
        "description": item.get("description"),
        "fields": {
            "summary": item.get("summary"),
            "labels": item.get("labels") or [],
            "description": item.get("description"),
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


def ensure_clean_worktree(repo: Path, allow_dirty: bool) -> None:
    status = run_git(repo, ["status", "--porcelain"])
    if status and not allow_dirty:
        raise WorkflowError(
            f"{repo} has uncommitted changes. Commit, stash, or rerun with allow_dirty=true."
        )


def create_work_packet(repo: Path, issue: Dict[str, Any], branch: str, profile_name: Optional[str]) -> Path:
    key = issue_key(issue, "JIRA")
    packet_dir = repo / ".hermes" / "work"
    packet_dir.mkdir(parents=True, exist_ok=True)
    packet = packet_dir / f"{key}.md"
    guidance = find_repo_guidance(repo)
    guidance_lines = "\n".join(f"- {path.relative_to(repo)}" for path in guidance) or "- None found"
    body = f"""# {key}: {issue_summary(issue)}

Jira: {issue_url(issue, key)}
Branch: {branch}
Hermes profile: {profile_name or env("HERMES_PROFILE", "default")}

## Repository Guidance

Read these files before editing:
{guidance_lines}

## Work Instructions

1. Use the official Atlassian Jira MCP issue details as source of truth.
2. Read repository guidance before editing.
3. Inspect the existing implementation and tests.
4. Make the requested change with the smallest reasonable scope.
5. Run relevant checks.
6. Commit only intended changes.
7. Wait for user review before PR creation.
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
    existing_branches = run_git(repo, ["branch", "--list", branch])
    if existing_branches:
        run_git(repo, ["checkout", branch], capture=False)
    else:
        run_git(repo, ["checkout", "-b", branch], capture=False)
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
    repo = expand_path(args["repo_path"])
    if args.get("stage_all"):
        run_git(repo, ["add", "-A"], capture=False)
    staged = run_git(repo, ["diff", "--cached", "--name-only"])
    if not staged:
        raise WorkflowError("No staged changes to commit. Stage files or pass stage_all=true.")
    jira_key = args["jira_key"]
    summary = args.get("summary") or jira_key
    message = args.get("message") or f"{jira_key}: {summary}"
    run_git(repo, ["commit", "-m", message], capture=False)
    return {
        "success": True,
        "repository": str(repo),
        "commit": run_git(repo, ["rev-parse", "HEAD"]),
        "message": message,
    }


def parse_remote(remote_url: str) -> Dict[str, Optional[str]]:
    patterns = [
        ("github", r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$"),
        ("bitbucket", r"bitbucket\.org[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$"),
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


def provider_status() -> Dict[str, Any]:
    return {
        "success": True,
        "atlassian_mcp": {
            "server_name": env("HERMES_ATLASSIAN_MCP_SERVER", "atlassian"),
            "url_env": "ATLASSIAN_MCP_URL",
            "configured": bool(env("ATLASSIAN_MCP_URL") or env("HERMES_ATLASSIAN_MCP_SERVER")),
            "note": "Configure the official Atlassian Jira/Rovo MCP server in ~/.hermes/config.yaml.",
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
    repo = expand_path(args["repo_path"])
    remote_url = run_git(repo, ["remote", "get-url", "origin"])
    parsed = parse_remote(remote_url)
    provider = args.get("provider") or "auto"
    if provider == "auto":
        provider = parsed.get("provider")
    if provider not in {"github", "bitbucket"}:
        raise WorkflowError("Could not infer provider. Pass provider=github or provider=bitbucket.")

    jira_key = args["jira_key"]
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
