# yira

Local-first JIRA task manager — edit tasks as YAML files, push changes to JIRA Cloud via CLI.

## How it works

1. **Pull** issues from JIRA → saved as individual YAML files in `tasks/`
2. **Edit** the YAML files locally (summary, description, points, status, etc.)
3. **Push** only the tasks you changed back to JIRA

Tasks are hashed on pull/push. When you edit a YAML file, the tool detects the change and marks it as dirty — so you never accidentally push unchanged tasks.

## Setup

### Requirements

- Python 3.10+
- A JIRA Cloud instance
- A JIRA API token ([generate one here](https://id.atlassian.com/manage-profile/security/api-tokens))

### Install

```bash
pip install yira
```

Or from source:

```bash
git clone https://github.com/Duilioizzi/yira.git
cd yira
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Configure

```bash
cp config.sample.yaml config.yaml
```

Edit `config.yaml` with your credentials:

```yaml
jira:
  server: "https://your-domain.atlassian.net"
  email: "you@example.com"
  api_token: "your-api-token"
  default_project: "PROJ"

tasks_dir: "tasks"
```

> `config.yaml` is in `.gitignore` — never commit it.

You can also use environment variables instead: `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`.

## Usage

### Pull issues from JIRA

```bash
# Pull all issues from default project
yira pull

# Pull current sprint only
yira pull --sprint

# Pull with a specific assignee
yira pull --assignee me

# Pull from a different project
yira pull --project TEAM

# Custom JQL
yira pull --jql "project = PROJ AND status = 'In Progress'"
```

Each issue is saved as `tasks/<KEY>.yaml`, e.g. `tasks/PROJ-123.yaml`.

### Edit tasks locally

Open any YAML file and change the fields you need:

```yaml
key: PROJ-123
summary: Implement login endpoint
description: ''
issuetype: Story
status: In Progress
priority: High
assignee: John Doe
story_points: 5.0
epic_link: PROJ-10
fix_version: '2026.03'
labels:
  - backend
  - auth
components:
  - api
```

### See what changed

```bash
# List all local tasks
yira list

# List only modified tasks
yira list --dirty

# Filter by status, assignee, or epic
yira list --status "In Progress"
yira list --assignee john
yira list --epic PROJ-10
```

### Push changes to JIRA

```bash
# Push specific tasks by key
yira push PROJ-123 PROJ-456

# Push all modified tasks
yira push --dirty

# Preview what would be pushed (no API calls)
yira push --dry-run

# Also transition the issue status
yira push PROJ-123 --transition
```

### Compare local vs remote

```bash
yira diff PROJ-123
```

Shows field-by-field differences between your local YAML and what's currently in JIRA.

### Create new tasks

```bash
# Create locally first
yira create -s "New login page" --type Story --points 3 --epic PROJ-10

# Create directly on JIRA (with assignee and epic)
yira create -s "Fix logout bug" --type Bug -a "John Doe" --epic PROJ-10 --push-now

# Push all locally-created tasks to JIRA
yira push-new
```

Locally-created tasks are saved as `NEW-0001.yaml`, `NEW-0002.yaml`, etc. Running `yira push-new` creates them on JIRA and renames the files to their real keys.

### Release management

```bash
# List all versions in the project
yira release list

# Create a new version
yira release create "2026.04" -d "April release" --start-date 2026-04-01 --release-date 2026-04-30

# Assign tasks to a version (updates JIRA + local YAML)
yira release assign "2026.04" PROJ-123 PROJ-456 PROJ-789

# View release status — tasks grouped by status with SP progress
yira release status "2026.04"

# Generate markdown release notes
yira release notes "2026.04"
yira release notes "2026.04" -o releases/2026.04.md

# Mark a version as released (+ optional notes)
yira release ship "2026.04" -n releases/2026.04.md
```

## Task fields

| Field | Description |
|-------|-------------|
| `key` | JIRA issue key (e.g. PROJ-123) |
| `summary` | Issue title |
| `description` | Full description |
| `issuetype` | Task, Story, Bug, Epic |
| `status` | Current status (To Do, In Progress, Done, etc.) |
| `priority` | Highest, High, Medium, Low, Lowest |
| `assignee` | Display name |
| `duedate` | Due date (YYYY-MM-DD) |
| `startdate` | Start date (YYYY-MM-DD) |
| `story_points` | Numeric estimate |
| `epic_link` | Parent epic key |
| `epic_name` | Epic name (for epic issues) |
| `sprint` | Sprint name |
| `fix_version` | Release / fixVersion name |
| `labels` | List of labels |
| `components` | List of components |

## Project structure

```
yira/
├── yira/
│   ├── cli.py        # CLI commands (click)
│   ├── client.py     # JIRA SDK wrapper
│   ├── config.py     # Config loader
│   └── models.py     # Task model + YAML I/O + dirty tracking
├── config.sample.yaml
├── pyproject.toml
└── .gitignore
```

## Multi-project / multi-team usage

The tool uses `default_project` from config but every command accepts `--project` to override it. Different teams can maintain their own `config.yaml` pointing to their project, or use separate `tasks/` directories via the `tasks_dir` config option.
