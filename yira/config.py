"""Configuration loader — reads JIRA credentials and project settings."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILENAME = "config.yaml"
DEFAULT_TASKS_DIR = "tasks"


@dataclass
class JiraConfig:
    server: str
    email: str
    api_token: str
    default_project: str | None = None


@dataclass
class AppConfig:
    jira: JiraConfig
    tasks_dir: Path = Path(DEFAULT_TASKS_DIR)

    # Fields that should be synced from JIRA (extend as needed)
    sync_fields: list[str] = field(default_factory=lambda: [
        "summary", "description", "status", "assignee", "priority",
        "story_points", "epic_link", "epic_name", "fix_version", "labels", "sprint",
        "issuetype", "components",
    ])


def find_config_path() -> Path:
    """Walk up from cwd to find config.yaml, falling back to .config/."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / CONFIG_FILENAME
        if candidate.exists():
            return candidate
    # Fallback: project-local .config dir
    return cwd / CONFIG_FILENAME


def load_config(path: Path | None = None) -> AppConfig:
    """Load and validate configuration from YAML file."""
    path = path or find_config_path()
    if not path.exists():
        print(
            f"Config file not found. Create '{CONFIG_FILENAME}' — "
            "see config.sample.yaml for reference.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(path) as f:
        raw = yaml.safe_load(f)

    jira_section = raw.get("jira", {})

    # Allow env-var overrides for CI / shared setups
    jira = JiraConfig(
        server=os.environ.get("JIRA_SERVER", jira_section.get("server", "")),
        email=os.environ.get("JIRA_EMAIL", jira_section.get("email", "")),
        api_token=os.environ.get("JIRA_API_TOKEN", jira_section.get("api_token", "")),
        default_project=jira_section.get("default_project"),
    )

    if not all([jira.server, jira.email, jira.api_token]):
        print(
            "Missing JIRA credentials. Provide server, email, and api_token "
            "in config.yaml or via JIRA_SERVER / JIRA_EMAIL / JIRA_API_TOKEN env vars.",
            file=sys.stderr,
        )
        sys.exit(1)

    tasks_dir = Path(raw.get("tasks_dir", DEFAULT_TASKS_DIR))

    return AppConfig(jira=jira, tasks_dir=tasks_dir)
