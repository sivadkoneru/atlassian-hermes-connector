from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools
import workflow


class HermesWorkflowTests(unittest.TestCase):
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

    def test_parse_remote_supports_github_and_bitbucket(self) -> None:
        github = workflow.parse_remote("git@github.com:owner/example.git")
        bitbucket = workflow.parse_remote("https://bitbucket.org/team/example.git")
        self.assertEqual(github["provider"], "github")
        self.assertEqual(github["owner"], "owner")
        self.assertEqual(bitbucket["provider"], "bitbucket")
        self.assertEqual(bitbucket["owner"], "team")

    def test_pr_plan_blocks_without_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            workflow.run_git(repo, ["init"], capture=True)
            workflow.run_git(repo, ["checkout", "-b", "feature/her-2"], capture=True)
            workflow.run_git(repo, ["remote", "add", "origin", "git@github.com:owner/example.git"])
            result = workflow.pr_plan({"repo_path": str(repo), "jira_key": "HER-2", "summary": "Add tests"})
            self.assertFalse(result["success"])
            self.assertTrue(result["blocked"])
            self.assertIn("review", result["reason"].lower())

    def test_pr_plan_returns_mcp_call_when_provider_tool_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            workflow.run_git(repo, ["init"], capture=True)
            workflow.run_git(repo, ["checkout", "-b", "feature/her-3"], capture=True)
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


if __name__ == "__main__":
    unittest.main()
