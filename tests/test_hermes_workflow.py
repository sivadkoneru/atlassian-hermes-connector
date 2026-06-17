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


if __name__ == "__main__":
    unittest.main()
