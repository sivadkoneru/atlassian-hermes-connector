# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **Hermes plugin** (not a standalone app) that coordinates four systems for Jira-backed development work, deliberately keeping their boundaries visible:

- **Jira** — source of truth for ticket content, reached through the *official Atlassian MCP* (configured in Hermes, **not bundled here**).
- **Hermes Kanban** — local operational board, stored as a single JSON file.
- **Git** — source of truth for local branch and commit state.
- **GitHub / Bitbucket** — owns PR creation, via provider MCP tool or an approved `gh`/`bb` CLI fallback.

Pure Python standard library, `requires-python >= 3.9`. There are **no third-party dependencies** — do not add any.

## Commands

```bash
# Run all tests (unittest, not pytest)
python3 -m unittest discover -s tests

# Run a single test
python3 -m unittest tests.test_hermes_workflow.HermesWorkflowTests.test_slugify_keeps_branch_names_compact

# Compile check (matches README validation step)
env PYTHONPYCACHEPREFIX=/private/tmp/atlassian-hermes-pycache \
  python3 -m py_compile __init__.py schemas.py tools.py workflow.py scripts/ticket.py scripts/jira_cron_sync.py

# Exercise the whole workflow locally without Hermes (board → item → resolve → start → commit → pr-plan)
python3 scripts/ticket.py status
python3 scripts/ticket.py <subcommand> --help

# Cron-safe assigned-Jira importer (expects Jira issue JSON exported via Atlassian MCP)
python3 scripts/jira_cron_sync.py --issues-json /tmp/issues.json --assignee you@example.com --profile default
```

## Architecture: the four-layer plugin surface

Adding or changing a tool touches **all of these in lockstep** — they map 1:1 by tool name:

1. `plugin.yaml` — manifest; tool name listed under `provides_tools`.
2. `schemas.py` — the JSON schema the model sees (one `UPPER_SNAKE` dict per tool).
3. `tools.py` — thin Hermes handler boundary. Each handler is one line delegating to `workflow`.
4. `workflow.py` — **all deterministic logic lives here** (~1700 lines). This is the only file with real behavior.
5. `__init__.py` — registers each `(schema, handler)` pair in the `registrations` list, adds the tool name to `TOOL_NAMES`, registers the hook, the `kanban-status` command, and bundled `skills/`.

**Error/return contract:** `workflow` functions take a single `args: Dict` and return a `Dict`. `tools._wrap` serializes the result to a JSON string, catching `WorkflowError` (expected, user-correctable failures — raise this for anything a user can fix) and falling back to a generic catch for everything else. Never return raw dicts from `tools.py` handlers; always go through `_wrap` (or `json_result`).

**Dual-import pattern**: `tools.py` does `from . import workflow` with a fallback to `import workflow`, so it runs both as a Hermes package and as a top-level test module. The two `scripts/` entry points instead prepend the repo root to `sys.path` and import `workflow` directly. Preserve whichever pattern the surrounding file uses when adding modules.

**State writes:** `build_kanban_item` merges one issue into an in-memory state dict; `create_kanban_item` and `sync_assigned_kanban_items` own the surrounding `load_state()` / `save_state()`, so a batch import writes the file once. `save_state` writes to a temp file and `os.replace`s it, so an interrupted run never leaves truncated JSON. Add batch operations the same way — do not call `create_kanban_item` in a loop.

## Key workflow concepts (each requires reading several functions in workflow.py)

- **MCP tool discovery** (`discover_atlassian_mcp_tools`): the plugin never hardcodes Atlassian tool names. It tokenizes whatever tool names Hermes exposes and scores them against pattern sets in `ATLASSIAN_MCP_ACTIONS` to map them to canonical actions (`search_issues`, `get_issue`, `create_issue`, `assign_issue`, `transition_issue`, `get_myself`). Confluence tokens are penalized. Sources: `tools`/`tool_catalog` args, `catalog_path`, `HERMES_MCP_TOOL_CATALOG`, `HERMES_ATLASSIAN_MCP_TOOLS`.

- **Repository resolution** (`resolve_repository` / `score_repo`): scored matching, highest wins; an exact score tie raises an ambiguity error. Each repo keeps its **best** hint, never the first that matched. Hint precedence: explicit `repo` arg → `repo:`/`repository:` Jira label → `Repository:` in description → `HERMES_JIRA_REPO_FIELD` custom field → `description` arg, weighted by `HINT_SOURCE_EXACT` / `HINT_SOURCE_SUBSTRING`. The bands are deliberate: max config bonus (15) < the 20-point gap between sources, and best substring + bonus (115) < weakest exact (200), so project/label/component signals can only break ties among equal hints and an exact match always beats a fuzzy one. **Preserve those inequalities when touching the weights.** Substring matching is whole-segment (`slug_contains_segment`), so `api` matches `api-gateway` but not `rapid`; use `aliases` for prefixes. With no configured repos, it falls back to discovering git dirs under `roots`.

- **Status → column mapping** (`status_column_map` / `kanban_column_for_issue`): Jira status drives Kanban placement via `DEFAULT_STATUS_COLUMN_MAP`, overridable by `statusColumns` in `HERMES_REPO_MAP`. Active blockers (from Jira issue links) route an item to the `Blocked` column when the board defines one. Sync **moves** existing items on Jira transitions rather than treating the board as a separate status source.

- **Branch naming** (`branch_name_for_issue`): deterministic `<branch-type>/<jira-key-lower>-<summary-slug>`. An explicit `branch:`/`branch-type:`/`type:` label wins outright. Otherwise branch **types** are tried in `DEFAULT_BRANCH_TYPES` order (`hotfix`, `bugfix`, `feature`, `docs`, `test`, `chore`; config-added types last) and the first type matching on any of labels → issue type → status → priority wins. Type order therefore outranks field order — a `Bug` labelled `hotfix`/`p1` resolves to `hotfix`, by design. Extendable via `branchTypes` in config; falls back to `HERMES_BRANCH_DEFAULT_TYPE`.

- **Automation gates** (`require_confirmation`): modes are `manual` / `semi` / `auto`. `manual`/`semi` require an explicit `confirm_branch` / `confirm_commit` flag before the action runs; `auto` proceeds. PR creation is separately gated by `HERMES_REQUIRE_REVIEW_BEFORE_PR` (default true) — `pr_plan` returns a `blocked` result until called with `reviewed=true`.

- **Work packet** (`create_work_packet`): `start_branch` writes `.hermes/work/<ISSUE_KEY>.md` into the *target* repo, carrying Jira summary, status, comments, dependency/blocker context, and a pointer to read that repo's `CLAUDE.md` / `AGENT.md` / `README.md` before editing.

- **ADF handling** (`flatten_adf`): Jira descriptions/comments arrive as Atlassian Document Format; flatten before use.

## State & config

- Kanban state is a single JSON file at `HERMES_KANBAN_STATE_PATH` (default `~/.hermes/atlassian-hermes-connector/kanban.json`), shaped by `initial_state()`. Items key off `item_id_from_key` (slugified Jira key); re-creating an item merges over the existing one.
- Config comes from `HERMES_REPO_MAP` (JSON; see `config/repositories.example.json`) or, if unset, falls back to individual `HERMES_*` env vars. Use the `env()` helper for reads — it treats `""`, `None`, and unexpanded `${...}` placeholders as unset.
- Full env reference: `.env.example` and `docs/configuration.md`. Hermes-side wiring: `config/hermes.config.example.yaml`. Architecture/workflow narrative: `docs/architecture.md`, `docs/workflow.md`.

## Conventions

- Tests are `unittest`-based and isolate state with `tempfile.TemporaryDirectory()` + `mock.patch.dict(os.environ, ...)` setting `HERMES_KANBAN_STATE_PATH` (and often `HERMES_REPO_MAP=""`). Follow that pattern; do not write to real `~/.hermes` paths in tests.
- `scripts/ticket.py` mirrors every tool as a CLI subcommand — keep it in sync when you add a tool so the workflow stays testable outside Hermes.
- Secrets stay out of the repo; `.env.example` holds placeholders only.
