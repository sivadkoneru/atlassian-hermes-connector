---
name: create-reviewed-pr
description: Prepare and create a pull request for completed Hermes Jira work after user review. Use when a Jira-backed branch has been implemented and committed, and the user approves opening a GitHub or Bitbucket pull request with a clear title and description.
version: 1.0.0
author: Siva Koneru
license: MIT
metadata:
  hermes:
    tags: [Pull Request, GitHub, Bitbucket, Review]
    requires_tools: [pr_plan]
---

# Create Reviewed PR

Use this skill only after the user has reviewed the implementation summary and approved pull request creation.

## Preview

Preview the PR before creating it:

```bash
python3 scripts/ticket.py pr-plan --repo-path <REPO> --jira-key <ISSUE_KEY>
```

Confirm the provider, repository, branch, base branch, title, and description.

## Create

Use `git` for branch state and push. For PR creation, use a configured GitHub or Bitbucket MCP tool first. If provider MCP is not available, ask the user whether to use `gh` or `bb`. If neither MCP nor provider CLI is configured, do not proceed.

After approval, preview the creation path:

```bash
python3 scripts/ticket.py pr-plan --repo-path <REPO> --jira-key <ISSUE_KEY> --reviewed
```

Use `--provider github` or `--provider bitbucket` when remote detection is ambiguous.

## PR Content

Use a title like:

```text
<ISSUE_KEY>: <Jira summary>
```

Include the Jira link, concise implementation summary, tests or checks run, and any review notes. Do not include secrets, private tokens, or unrelated local paths.
