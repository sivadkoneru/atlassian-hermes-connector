# Configuration

## Repository Map

Point `HERMES_REPO_MAP` to a JSON file with this shape:

```json
{
  "roots": ["~/Development"],
  "profiles": {
    "default": {
      "displayName": "Primary Developer",
      "jiraAccountId": "account-id"
    }
  },
  "repositories": [
    {
      "name": "service-name",
      "path": "~/Development/service-name",
      "provider": "github",
      "aliases": ["service"],
      "jira": {
        "projects": ["APP"],
        "components": ["backend"],
        "labels": ["repo:service-name"]
      }
    }
  ]
}
```

## Jira Repository Hints

Preferred repository hints:

- Jira label: `repo:<repository-name>`
- Jira custom field named by `HERMES_JIRA_REPO_FIELD`
- configured project, component, and label mappings

## Providers

GitHub PR creation uses the GitHub CLI:

```bash
gh auth status
```

Bitbucket PR creation uses the Bitbucket 2.0 API with `BITBUCKET_USERNAME` and `BITBUCKET_APP_PASSWORD`.
