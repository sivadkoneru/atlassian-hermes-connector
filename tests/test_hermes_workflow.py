from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools
import workflow

TICKET_CLI = Path(__file__).resolve().parents[1] / "scripts" / "ticket.py"

# Plugin behavior is env-driven, so a developer's own HERMES_* settings would
# otherwise leak into every test. Each test starts from a known-empty config.
PLUGIN_ENV_VARS = [
    "HERMES_ATLASSIAN_MCP_SERVER",
    "HERMES_ATLASSIAN_MCP_TOOLS",
    "HERMES_AUTOMATION_MODE",
    "HERMES_BRANCH_DEFAULT_TYPE",
    "HERMES_BRANCH_FORCE_PREFIX",
    "HERMES_DEVELOPMENT_ROOT",
    "HERMES_GITHUB_MCP_TOOL",
    "HERMES_BITBUCKET_MCP_TOOL",
    "HERMES_JIRA_ASSIGNEE",
    "HERMES_JIRA_REPO_FIELD",
    "HERMES_KANBAN_BOARD",
    "HERMES_KANBAN_STATE_PATH",
    "HERMES_MCP_TOOL_CATALOG",
    "HERMES_PR_BASE_BRANCH",
    "HERMES_PROFILE",
    "HERMES_PROJECT_KEY",
    "HERMES_PROJECT_NAME",
    "HERMES_REPO_MAP",
    "HERMES_REQUIRE_REVIEW_BEFORE_PR",
]


class PluginEnvTestCase(unittest.TestCase):
    """Neutralizes ambient HERMES_* configuration; `env()` reads "" as unset."""

    def setUp(self) -> None:
        state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(state_dir.cleanup)
        overrides = {name: "" for name in PLUGIN_ENV_VARS}
        # An empty state path would fall back to the real ~/.hermes file, so point
        # unpatched tests at a temp file instead of relying on them to remember.
        overrides["HERMES_KANBAN_STATE_PATH"] = str(Path(state_dir.name) / "kanban.json")
        patcher = mock.patch.dict(os.environ, overrides, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)


class HermesWorkflowTests(PluginEnvTestCase):
    def test_slugify_keeps_branch_names_compact(self) -> None:
        self.assertEqual(workflow.slugify("Add OAuth callback validation!"), "add-oauth-callback-validation")

    def test_create_board_and_assign_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "kanban.json"
            with mock.patch.dict(os.environ, {"HERMES_KANBAN_STATE_PATH": str(state_path)}, clear=False):
                board = workflow.ensure_board({"project_key": "HER", "board_name": "Delivery"})
                item = workflow.create_kanban_item(
                    {
                        "jira_key": "HER-1",
                        "summary": "Ship deployment workflow",
                        "description": "Repository: deploy-service",
                        "profile": "release",
                    }
                )
                assigned = workflow.assign_profile(
                    {
                        "jira_key": "HER-1",
                        "profile": "release",
                        "profile_data": {"deployment": "staging"},
                    }
                )
                self.assertEqual(board["project"]["key"], "HER")
                self.assertEqual(item["item"]["repo_hints"][0]["value"], "deploy-service")
                self.assertEqual(assigned["item"]["profile"], "release")

    def test_refreshing_an_item_keeps_assigned_profile_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"HERMES_KANBAN_STATE_PATH": str(Path(tmp) / "kanban.json")}, clear=False
            ):
                workflow.create_kanban_item({"jira_key": "HER-1", "summary": "Ship it"})
                workflow.assign_profile(
                    {"jira_key": "HER-1", "profile": "release", "profile_data": {"deployment": "staging"}}
                )
                refreshed = workflow.create_kanban_item({"jira_key": "HER-1", "summary": "Ship it, revised"})
                workflow.sync_assigned_kanban_items(
                    {
                        "issues": [
                            {
                                "key": "HER-1",
                                "fields": {"summary": "Ship it", "assignee": {"emailAddress": "dev@example.com"}},
                            }
                        ],
                        "assignee": "dev@example.com",
                    }
                )
                synced = workflow.load_state()["items"][0]

            self.assertEqual(refreshed["item"]["profile"], "release")
            self.assertEqual(refreshed["item"]["profile_data"]["deployment"], "staging")
            self.assertEqual(synced["profile"], "release")
            self.assertEqual(synced["profile_data"]["deployment"], "staging")

    def test_creating_item_with_new_profile_drops_stale_profile_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"HERMES_KANBAN_STATE_PATH": str(Path(tmp) / "kanban.json")}, clear=False
            ):
                workflow.create_kanban_item({"jira_key": "HER-1", "summary": "Ship it"})
                workflow.assign_profile(
                    {"jira_key": "HER-1", "profile": "release", "profile_data": {"deployment": "staging"}}
                )
                switched = workflow.create_kanban_item(
                    {"jira_key": "HER-1", "summary": "Ship it, revised", "profile": "hotfix"}
                )

            self.assertEqual(switched["item"]["profile"], "hotfix")
            self.assertNotEqual(switched["item"].get("profile_data", {}).get("deployment"), "staging")

    def test_repo_hint_prefers_tagged_repo_label(self) -> None:
        issue = {
            "fields": {
                "labels": ["repo:tagged-service"],
                "description": "Repository: description-service",
            }
        }
        self.assertEqual(workflow.repo_hint_from_issue(issue), "tagged-service")

    def test_repo_hint_from_description_when_label_missing(self) -> None:
        issue = {"fields": {"labels": [], "description": "Repository: description-service"}}
        self.assertEqual(workflow.repo_hint_from_issue(issue), "description-service")

    def test_branch_name_uses_jira_issue_type(self) -> None:
        bug = {
            "key": "HER-10",
            "fields": {
                "summary": "Fix login redirect",
                "issuetype": {"name": "Bug"},
                "labels": [],
            },
        }
        story = {
            "key": "HER-11",
            "fields": {
                "summary": "Add dashboard",
                "issuetype": {"name": "Story"},
                "labels": [],
            },
        }
        self.assertEqual(workflow.branch_name_for_issue(bug), "bugfix/her-10-fix-login-redirect")
        self.assertEqual(workflow.branch_name_for_issue(story), "feature/her-11-add-dashboard")

    def test_urgency_label_outranks_issue_type(self) -> None:
        urgent_bug = {
            "key": "HER-13",
            "fields": {"summary": "Restore webhook", "issuetype": {"name": "Bug"}, "labels": ["hotfix"]},
        }
        urgent_task = {
            "key": "HER-14",
            "fields": {"summary": "Rotate token", "issuetype": {"name": "Task"}, "labels": ["p1"]},
        }
        self.assertEqual(workflow.branch_type_for_issue(urgent_bug), "hotfix")
        self.assertEqual(workflow.branch_type_for_issue(urgent_task), "hotfix")

    def test_branch_label_override_wins(self) -> None:
        issue = {
            "key": "HER-12",
            "fields": {
                "summary": "Update runbook",
                "issuetype": {"name": "Task"},
                "labels": ["branch:docs"],
            },
        }
        self.assertEqual(workflow.branch_name_for_issue(issue), "docs/her-12-update-runbook")

    def test_resolve_repository_from_configured_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "service"
            (repo / ".git").mkdir(parents=True)
            issue = {
                "key": "APP-1",
                "fields": {
                    "summary": "Fix service auth",
                    "project": {"key": "APP"},
                    "labels": ["repo:service"],
                    "components": [{"name": "backend"}],
                },
            }
            config = {
                "repositories": [
                    {
                        "name": "service",
                        "path": str(repo),
                        "provider": "github",
                        "jira": {
                            "projects": ["APP"],
                            "labels": ["repo:service"],
                            "components": ["backend"],
                        },
                    }
                ]
            }
            match = workflow.resolve_repository(issue, config)
            self.assertEqual(match.path, repo.resolve())
            self.assertEqual(match.provider, "github")
            self.assertEqual(match.hint_source, "jira_label")

    @staticmethod
    def repo_config(tmp: str, *names: str) -> dict:
        repos = []
        for name in names:
            (Path(tmp) / name / ".git").mkdir(parents=True)
            repos.append({"name": name, "path": str(Path(tmp) / name)})
        return {"repositories": repos}

    def test_exact_hint_beats_earlier_weak_substring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.repo_config(tmp, "payments-service", "billing-service")
            config["repositories"][1]["jira"] = {"projects": ["PAY"]}
            issue = {
                "key": "PAY-1",
                "fields": {
                    "summary": "Add refunds",
                    "project": {"key": "PAY"},
                    "labels": ["repo:service"],
                    "description": "Repository: payments-service",
                },
            }

            match = workflow.resolve_repository(issue, config)

            self.assertEqual(match.name, "payments-service")
            self.assertEqual(match.hint_source, "jira_description")

    def test_explicit_repo_arg_outranks_conflicting_jira_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.repo_config(tmp, "alpha-service", "beta-service")
            config["repositories"][1]["jira"] = {"projects": ["HER"], "components": ["backend"]}
            issue = {
                "key": "HER-1",
                "fields": {
                    "summary": "Tune cache",
                    "project": {"key": "HER"},
                    "labels": ["repo:beta-service"],
                    "components": [{"name": "backend"}],
                },
            }

            match = workflow.resolve_repository(issue, config, "alpha-service")

            self.assertEqual(match.name, "alpha-service")
            self.assertEqual(match.hint_source, "explicit")

    def test_substring_hint_matches_whole_segments_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.repo_config(tmp, "api-gateway", "rapid")
            issue = {"key": "HER-2", "fields": {"summary": "Route calls", "description": "Repository: api"}}

            match = workflow.resolve_repository(issue, config)

            self.assertEqual(match.name, "api-gateway")

    def test_config_signals_never_outrank_an_exact_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.repo_config(tmp, "platform", "payments-service")
            config["repositories"][0]["jira"] = {
                "projects": ["PAY"],
                "labels": ["backend"],
                "components": ["core"],
            }
            issue = {
                "key": "PAY-3",
                "fields": {
                    "summary": "Ship platform change",
                    "project": {"key": "PAY"},
                    "labels": ["backend"],
                    "components": [{"name": "core"}],
                    "description": "Repository: payments-service",
                },
            }

            match = workflow.resolve_repository(issue, config)

            self.assertEqual(match.name, "payments-service")

    def test_duplicate_alias_still_reports_ambiguous_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.repo_config(tmp, "first-service", "second-service")
            for repo in config["repositories"]:
                repo["aliases"] = ["service"]
            issue = {"key": "HER-4", "fields": {"summary": "Fix it", "labels": ["repo:service"]}}

            with self.assertRaises(workflow.WorkflowError) as caught:
                workflow.resolve_repository(issue, config)

            self.assertIn("ambiguous", str(caught.exception))

    def test_discovers_git_repositories_and_skips_unusable_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Development"
            (root / "service-a" / ".git").mkdir(parents=True)
            (root / "not-a-repo").mkdir()
            not_a_directory = Path(tmp) / "file-root"
            not_a_directory.write_text("", encoding="utf-8")

            repos = workflow.discover_git_repositories(
                {"roots": [str(root), str(Path(tmp) / "missing"), str(not_a_directory)]}
            )

            self.assertEqual([repo["name"] for repo in repos], ["service-a"])

    def test_parse_remote_supports_github_and_bitbucket(self) -> None:
        github = workflow.parse_remote("git@github.com:owner/example.git")
        bitbucket = workflow.parse_remote("https://bitbucket.org/team/example.git")
        self.assertEqual(github["provider"], "github")
        self.assertEqual(github["owner"], "owner")
        self.assertEqual(bitbucket["provider"], "bitbucket")
        self.assertEqual(bitbucket["owner"], "team")

    def test_parse_remote_handles_dotted_names_and_trailing_slash(self) -> None:
        dotted = workflow.parse_remote("https://github.com/owner/my.service.git")
        trailing = workflow.parse_remote("https://bitbucket.org/team/example/")
        self.assertEqual((dotted["provider"], dotted["owner"], dotted["repo"]), ("github", "owner", "my.service"))
        self.assertEqual((trailing["provider"], trailing["owner"], trailing["repo"]), ("bitbucket", "team", "example"))

    def test_missing_required_arguments_report_workflow_errors(self) -> None:
        with self.assertRaises(workflow.WorkflowError):
            workflow.pr_plan({"jira_key": "HER-1"})
        with self.assertRaises(workflow.WorkflowError):
            workflow.commit_work({"jira_key": "HER-1", "confirm_commit": True})
        error = json.loads(tools.pr_plan({"jira_key": "HER-1"}))
        self.assertFalse(error["success"])
        self.assertEqual(error["error"], "repo_path is required.")

    def test_pr_plan_blocks_without_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            workflow.run_git(repo, ["init"])
            workflow.run_git(repo, ["checkout", "-b", "feature/her-2"])
            workflow.run_git(repo, ["remote", "add", "origin", "git@github.com:owner/example.git"])
            result = workflow.pr_plan({"repo_path": str(repo), "jira_key": "HER-2", "summary": "Add tests"})
            self.assertFalse(result["success"])
            self.assertTrue(result["blocked"])
            self.assertIn("review", result["reason"].lower())

    def test_pr_plan_returns_mcp_call_when_provider_tool_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            workflow.run_git(repo, ["init"])
            workflow.run_git(repo, ["checkout", "-b", "feature/her-3"])
            workflow.run_git(repo, ["remote", "add", "origin", "git@github.com:owner/example.git"])
            with mock.patch.dict(os.environ, {"HERMES_GITHUB_MCP_TOOL": "mcp_github_create_pull_request"}):
                result = workflow.pr_plan(
                    {
                        "repo_path": str(repo),
                        "jira_key": "HER-3",
                        "summary": "Add provider bridge",
                        "reviewed": True,
                    }
                )
            creation = result["pull_request"]["creation"]
            self.assertTrue(result["success"])
            self.assertEqual(creation["mode"], "mcp")
            self.assertEqual(creation["tool"], "mcp_github_create_pull_request")
            self.assertEqual(creation["arguments"]["head"], "feature/her-3")
            self.assertEqual(creation["arguments"]["repository"], "example")

    def test_start_branch_manual_mode_requires_confirmation_before_repo_lookup(self) -> None:
        result = workflow.start_branch(
            {
                "jira_issue": {"key": "HER-4", "fields": {"summary": "Manual gate"}},
                "automation_mode": "manual",
            }
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(result["confirmation_key"], "confirm_branch")

    def test_tool_handler_returns_json_string(self) -> None:
        result = json.loads(tools.provider_status({}))
        self.assertTrue(result["success"])
        self.assertIn("atlassian_mcp", result)

    def test_discovers_atlassian_mcp_jira_actions(self) -> None:
        result = workflow.discover_atlassian_mcp_tools(
            {
                "tools": [
                    "mcp_atlassian_searchJiraIssues",
                    "mcp_atlassian_getJiraIssue",
                    "mcp_atlassian_createJiraIssue",
                    "mcp_atlassian_assignJiraIssue",
                    "mcp_atlassian_transitionJiraIssue",
                    "mcp_atlassian_getCurrentUser",
                    "mcp_github_create_pull_request",
                ]
            }
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["actions"]["search_issues"]["tool"], "mcp_atlassian_searchJiraIssues")
        self.assertEqual(result["actions"]["get_issue"]["tool"], "mcp_atlassian_getJiraIssue")
        self.assertEqual(result["actions"]["create_issue"]["tool"], "mcp_atlassian_createJiraIssue")
        self.assertEqual(result["actions"]["assign_issue"]["tool"], "mcp_atlassian_assignJiraIssue")
        self.assertEqual(result["actions"]["transition_issue"]["tool"], "mcp_atlassian_transitionJiraIssue")
        self.assertEqual(result["actions"]["get_myself"]["tool"], "mcp_atlassian_getCurrentUser")
        self.assertNotIn("mcp_github_create_pull_request", result["available_tools"])

    def test_provider_status_uses_configured_atlassian_tool_names(self) -> None:
        tools_csv = "mcp_atlassian_searchJiraIssues,mcp_atlassian_getJiraIssue"
        with mock.patch.dict(os.environ, {"HERMES_ATLASSIAN_MCP_TOOLS": tools_csv}, clear=False):
            result = workflow.provider_status()
        discovery = result["atlassian_mcp"]["discovery"]
        self.assertEqual(discovery["actions"]["search_issues"]["tool"], "mcp_atlassian_searchJiraIssues")
        self.assertEqual(discovery["actions"]["get_issue"]["tool"], "mcp_atlassian_getJiraIssue")
        self.assertIn("create_issue", discovery["missing_actions"])

    def test_sync_assigned_issues_filters_by_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "kanban.json"
            issues = [
                {
                    "key": "HER-20",
                    "fields": {
                        "summary": "Assigned work",
                        "issuetype": {"name": "Bug"},
                        "labels": ["repo:service"],
                        "assignee": {"emailAddress": "dev@example.com", "displayName": "Dev User"},
                    },
                },
                {
                    "key": "HER-21",
                    "fields": {
                        "summary": "Someone else",
                        "issuetype": {"name": "Story"},
                        "labels": ["repo:service"],
                        "assignee": {"emailAddress": "other@example.com", "displayName": "Other User"},
                    },
                },
            ]
            with mock.patch.dict(os.environ, {"HERMES_KANBAN_STATE_PATH": str(state_path)}, clear=False):
                result = workflow.sync_assigned_kanban_items(
                    {"issues": issues, "assignee": "dev@example.com", "profile": "default"}
                )
                state = workflow.load_state()
            self.assertEqual(result["created"], ["HER-20"])
            self.assertEqual(result["skipped"], [{"key": "HER-21", "reason": "assignee did not match"}])
            self.assertEqual([item["jira_key"] for item in state["items"]], ["HER-20"])
            self.assertEqual(state["items"][0]["branch_type"], "bugfix")

    def test_create_item_maps_jira_status_and_imports_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "kanban.json"
            issue = {
                "key": "HER-30",
                "fields": {
                    "summary": "Review deployment checks",
                    "status": {"name": "In Review"},
                    "comment": {
                        "comments": [
                            {
                                "id": "10001",
                                "author": {"displayName": "Reviewer"},
                                "created": "2026-06-17T08:00:00.000+0000",
                                "body": {
                                    "type": "doc",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "Check rollback notes."}],
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                },
            }
            with mock.patch.dict(
                os.environ,
                {"HERMES_KANBAN_STATE_PATH": str(state_path), "HERMES_REPO_MAP": ""},
                clear=False,
            ):
                item = workflow.create_kanban_item({"jira_issue": issue})["item"]

            self.assertEqual(item["status"], "Review")
            self.assertEqual(item["jira_status"], "In Review")
            self.assertEqual(item["comments"][0]["author"], "Reviewer")
            self.assertIn("rollback", item["comments"][0]["body"])

    def test_sync_moves_existing_item_to_current_jira_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "kanban.json"
            base_issue = {
                "key": "HER-31",
                "fields": {
                    "summary": "Build scheduler",
                    "issuetype": {"name": "Story"},
                    "status": {"name": "To Do"},
                    "assignee": {"emailAddress": "dev@example.com"},
                },
            }
            moved_issue = {
                "key": "HER-31",
                "fields": {
                    "summary": "Build scheduler",
                    "issuetype": {"name": "Story"},
                    "status": {"name": "In Progress"},
                    "assignee": {"emailAddress": "dev@example.com"},
                },
            }
            with mock.patch.dict(
                os.environ,
                {"HERMES_KANBAN_STATE_PATH": str(state_path), "HERMES_REPO_MAP": ""},
                clear=False,
            ):
                workflow.sync_assigned_kanban_items({"issues": [base_issue], "assignee": "dev@example.com"})
                result = workflow.sync_assigned_kanban_items(
                    {"issues": [moved_issue], "assignee": "dev@example.com"}
                )
                state = workflow.load_state()

            self.assertEqual(result["updated"], ["HER-31"])
            self.assertEqual(
                result["moved"],
                [{"key": "HER-31", "from": "Backlog", "to": "In Progress", "jira_status": "In Progress"}],
            )
            self.assertEqual(state["items"][0]["status"], "In Progress")

    def test_active_blocker_uses_blocked_column_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "kanban.json"
            config_path = Path(tmp) / "repos.json"
            config_path.write_text(
                json.dumps(
                    {
                        "project": {"key": "HER", "name": "Hermes Delivery"},
                        "board": {
                            "name": "Hermes Delivery Board",
                            "columns": ["Backlog", "Blocked", "In Progress", "Done"],
                        },
                        "roots": [tmp],
                        "repositories": [],
                    }
                ),
                encoding="utf-8",
            )
            issue = {
                "key": "HER-32",
                "fields": {
                    "summary": "Implement guarded deploy",
                    "status": {"name": "In Progress"},
                    "issuelinks": [
                        {
                            "type": {
                                "name": "Blocks",
                                "inward": "is blocked by",
                                "outward": "blocks",
                            },
                            "inwardIssue": {
                                "key": "HER-29",
                                "fields": {
                                    "summary": "Finish release toggle",
                                    "status": {"name": "In Progress"},
                                },
                            },
                        }
                    ],
                },
            }
            with mock.patch.dict(
                os.environ,
                {"HERMES_KANBAN_STATE_PATH": str(state_path), "HERMES_REPO_MAP": str(config_path)},
                clear=False,
            ):
                item = workflow.create_kanban_item({"jira_issue": issue})["item"]

            self.assertEqual(item["status"], "Blocked")
            self.assertTrue(item["blocked"])
            self.assertEqual(item["dependencies"]["active_blockers"][0]["key"], "HER-29")

    def test_start_branch_creates_then_reuses_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "service"
            repo.mkdir()
            workflow.run_git(repo, ["init"])
            workflow.run_git(repo, ["config", "user.email", "dev@example.com"])
            workflow.run_git(repo, ["config", "user.name", "Dev"])
            (repo / "README.md").write_text("service", encoding="utf-8")
            workflow.run_git(repo, ["add", "README.md"])
            workflow.run_git(repo, ["commit", "-m", "init"])
            config_path = Path(tmp) / "repos.json"
            config_path.write_text(
                json.dumps({"repositories": [{"name": "service", "path": str(repo)}]}),
                encoding="utf-8",
            )
            issue = {
                "key": "HER-40",
                "fields": {
                    "summary": "Add retry guard",
                    "issuetype": {"name": "Bug"},
                    "labels": ["repo:service"],
                },
            }
            env = {
                "HERMES_KANBAN_STATE_PATH": str(Path(tmp) / "kanban.json"),
                "HERMES_REPO_MAP": str(config_path),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                first = workflow.start_branch({"jira_issue": issue, "confirm_branch": True})
                # The work packet written by the first call must not count as a dirty worktree.
                second = workflow.start_branch({"jira_issue": issue, "confirm_branch": True})
                (repo / "README.md").write_text("edited", encoding="utf-8")
                with self.assertRaises(workflow.WorkflowError):
                    workflow.start_branch({"jira_issue": issue, "confirm_branch": True})

            self.assertEqual(first["branch"], "bugfix/her-40-add-retry-guard")
            self.assertEqual(second["branch"], first["branch"])
            self.assertEqual(
                workflow.run_git(repo, ["branch", "--show-current"]), "bugfix/her-40-add-retry-guard"
            )
            self.assertTrue(Path(first["work_packet"]).is_file())

    def test_commit_work_requires_staged_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            workflow.run_git(repo, ["init"])
            workflow.run_git(repo, ["config", "user.email", "dev@example.com"])
            workflow.run_git(repo, ["config", "user.name", "Dev"])
            args = {"repo_path": str(repo), "jira_key": "HER-41", "confirm_commit": True}

            with self.assertRaises(workflow.WorkflowError):
                workflow.commit_work(args)

            (repo / "change.txt").write_text("work", encoding="utf-8")
            result = workflow.commit_work({**args, "stage_all": True, "summary": "Add change"})

            self.assertTrue(result["success"])
            self.assertEqual(result["message"], "HER-41: Add change")
            self.assertEqual(
                workflow.run_git(repo, ["log", "-1", "--pretty=%s"]), "HER-41: Add change"
            )

    def test_cli_commit_prints_only_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            workflow.run_git(repo, ["init"])
            workflow.run_git(repo, ["config", "user.email", "dev@example.com"])
            workflow.run_git(repo, ["config", "user.name", "Dev"])
            (repo / "change.txt").write_text("work", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TICKET_CLI),
                    "commit",
                    "--repo-path",
                    str(repo),
                    "--jira-key",
                    "HER-42",
                    "--stage-all",
                    "--confirm-commit",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            # git's own chatter must never reach stdout and corrupt the JSON payload.
            self.assertTrue(json.loads(completed.stdout)["success"])

    def test_work_packet_rejects_path_traversal_in_jira_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git").mkdir()
            with self.assertRaises(workflow.WorkflowError):
                workflow.create_work_packet(repo, {"key": "../../escaped"}, "branch", None)
            self.assertFalse((repo.parent / "escaped.md").exists())

    def test_sync_registers_profile_without_extra_state_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "kanban.json"
            issues = [
                {
                    "key": f"HER-5{index}",
                    "fields": {"summary": f"Task {index}", "assignee": {"emailAddress": "dev@example.com"}},
                }
                for index in range(3)
            ]
            with mock.patch.dict(os.environ, {"HERMES_KANBAN_STATE_PATH": str(state_path)}, clear=False):
                with mock.patch.object(workflow, "save_state", wraps=workflow.save_state) as saver:
                    result = workflow.sync_assigned_kanban_items(
                        {"issues": issues, "assignee": "dev@example.com", "profile": "release"}
                    )
                state = workflow.load_state()

            self.assertEqual(result["created"], ["HER-50", "HER-51", "HER-52"])
            self.assertEqual(saver.call_count, 1)
            self.assertEqual(state["profiles"]["release"]["name"], "release")
            self.assertEqual({item["profile"] for item in state["items"]}, {"release"})

    def test_discovery_prefers_narrowest_jira_tool(self) -> None:
        result = workflow.discover_atlassian_mcp_tools(
            {
                "tools": [
                    "mcp_atlassian_getJiraIssueRemoteIssueLinks",
                    "mcp_atlassian_getJiraIssue",
                    "mcp_atlassian_searchJiraIssuesUsingJql",
                    "mcp_atlassian_getConfluencePage",
                ]
            }
        )
        self.assertEqual(result["actions"]["get_issue"]["tool"], "mcp_atlassian_getJiraIssue")
        self.assertEqual(
            result["actions"]["search_issues"]["tool"], "mcp_atlassian_searchJiraIssuesUsingJql"
        )

    def test_score_tool_for_action_floors_penalized_match_at_one(self) -> None:
        extra_tokens = "_".join(f"extra{i}" for i in range(40))
        tool_name = f"get_issue_with_{extra_tokens}"
        result = workflow.score_tool_for_action(tool_name, "get_issue", "atlassian")
        self.assertGreaterEqual(result["score"], 1)
        self.assertEqual(result["matched_pattern"], ["get", "issue"])

    def test_work_packet_includes_dependencies_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git").mkdir()
            issue = {
                "key": "HER-33",
                "fields": {
                    "summary": "Use ticket context",
                    "status": {"name": "In Progress"},
                    "comment": {
                        "comments": [
                            {
                                "author": {"displayName": "Product"},
                                "body": "Prefer the existing deployment service.",
                            }
                        ]
                    },
                    "issuelinks": [
                        {
                            "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                            "inwardIssue": {
                                "key": "HER-28",
                                "fields": {"summary": "Document API contract", "status": {"name": "To Do"}},
                            },
                        }
                    ],
                },
            }
            packet = workflow.create_work_packet(repo, issue, "feature/her-33-use-ticket-context", "default")
            body = packet.read_text(encoding="utf-8")

            self.assertIn("## Dependencies", body)
            self.assertIn("HER-28: Document API contract", body)
            self.assertIn("## Jira Comments", body)
            self.assertIn("Prefer the existing deployment service.", body)


class PluginRegistrationTests(PluginEnvTestCase):
    """plugin.yaml, schemas.py, tools.py, and __init__.py must stay in lockstep."""

    class FakeContext:
        def __init__(self) -> None:
            self.tools: dict = {}
            self.hooks: list = []
            self.commands: list = []
            self.skills: list = []

        def register_tool(self, name, toolset, schema, handler, description) -> None:
            self.tools[name] = (schema, handler, description)

        def register_hook(self, event, handler) -> None:
            self.hooks.append(event)

        def register_command(self, name, handler, description) -> None:
            self.commands.append(name)

        def register_skill(self, name, path) -> None:
            self.skills.append((name, Path(path)))

    @staticmethod
    def load_plugin_package():
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "hermes_plugin_under_test", root / "__init__.py", submodule_search_locations=[str(root)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def manifest_tool_names() -> list:
        """Read provides_tools from plugin.yaml without a YAML dependency."""
        names, in_block = [], False
        for raw_line in (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8").splitlines():
            if not raw_line.startswith((" ", "-")) and raw_line.strip().endswith(":"):
                in_block = raw_line.strip() == "provides_tools:"
            elif in_block and raw_line.strip().startswith("- "):
                names.append(raw_line.strip()[2:].strip())
        return names

    def test_manifest_schemas_and_handlers_match_registrations(self) -> None:
        plugin = self.load_plugin_package()
        ctx = self.FakeContext()
        plugin.register(ctx)
        registered = set(ctx.tools)

        self.assertEqual(registered, set(self.manifest_tool_names()), "plugin.yaml provides_tools drifted")
        self.assertEqual(registered, set(plugin.TOOL_NAMES), "__init__.TOOL_NAMES drifted")
        for name, (schema, handler, description) in ctx.tools.items():
            self.assertEqual(schema["name"], name)
            self.assertEqual(description, schema["description"])
            # The plugin package imports its own `tools` instance, so compare by name.
            self.assertEqual(handler.__name__, name, f"{name} is wired to handler {handler.__name__}")
            self.assertTrue(callable(getattr(tools, name, None)), f"tools.{name} is missing")
            self.assertEqual(schema["parameters"]["type"], "object")

    def test_hook_command_and_skills_are_registered(self) -> None:
        plugin = self.load_plugin_package()
        ctx = self.FakeContext()
        plugin.register(ctx)

        self.assertEqual(ctx.hooks, ["post_tool_call"])
        self.assertEqual(ctx.commands, ["kanban-status"])
        self.assertEqual([name for name, _ in ctx.skills], ["create-reviewed-pr", "jira-kanban", "work-jira-ticket"])
        for _, skill_path in ctx.skills:
            self.assertTrue(skill_path.is_file())

    def test_every_registered_tool_handler_returns_json(self) -> None:
        plugin = self.load_plugin_package()
        ctx = self.FakeContext()
        plugin.register(ctx)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"HERMES_KANBAN_STATE_PATH": str(Path(tmp) / "kanban.json")}, clear=False
            ):
                for name, (_schema, handler, _description) in ctx.tools.items():
                    # Called with no arguments, a handler must still return a JSON verdict,
                    # never raise into Hermes.
                    payload = json.loads(handler({}))
                    self.assertIn("success", payload, name)


if __name__ == "__main__":
    unittest.main()
