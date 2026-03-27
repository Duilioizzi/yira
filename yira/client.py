"""JIRA API client — wraps the jira SDK for pull/push operations."""

from __future__ import annotations

from jira import JIRA

from .config import AppConfig
from .models import Task


# JIRA custom-field names vary per instance.  These are the most common
# defaults for Cloud; override in config if yours differ.
CUSTOM_FIELDS = {
    "story_points": "customfield_10041",   # Story Points
    "epic_link": "customfield_10014",     # common Cloud default
    "epic_name": "customfield_10011",     # common Cloud default
    "sprint": "customfield_10020",        # common Cloud default
    "startdate": "customfield_10015",     # Start date
}


def connect(cfg: AppConfig) -> JIRA:
    """Create an authenticated JIRA client."""
    return JIRA(
        server=cfg.jira.server,
        basic_auth=(cfg.jira.email, cfg.jira.api_token),
    )


def _safe(value: object | None, attr: str = "name") -> str:
    """Extract a display string from a JIRA resource object, or ''."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return getattr(value, attr, str(value))


def _sprint_name(sprint_field: list | None) -> str:
    """Extract the active/future sprint name from the sprint field."""
    if not sprint_field:
        return ""
    for s in sprint_field:
        name = getattr(s, "name", None) or (s if isinstance(s, str) else "")
        if name:
            return str(name)
    return ""


def issue_to_task(issue, custom_fields: dict[str, str] | None = None) -> Task:
    """Convert a jira.Issue into a local Task."""
    cf = {**CUSTOM_FIELDS, **(custom_fields or {})}
    fields = issue.fields

    fix_versions = getattr(fields, "fixVersions", None) or []
    fix_version = fix_versions[0].name if fix_versions else ""

    return Task(
        key=issue.key,
        issue_id=str(issue.id),
        summary=fields.summary or "",
        description=fields.description or "",
        issuetype=_safe(fields.issuetype),
        status=_safe(fields.status),
        priority=_safe(fields.priority),
        assignee=_safe(fields.assignee, attr="displayName"),
        duedate=getattr(fields, "duedate", None) or "",
        startdate=getattr(fields, cf.get("startdate", "customfield_10015"), None) or "",
        story_points=getattr(fields, cf["story_points"], None),
        epic_link=getattr(fields, cf["epic_link"], None) or "",
        epic_name=getattr(fields, cf["epic_name"], None) or "",
        sprint=_sprint_name(getattr(fields, cf["sprint"], None)),
        fix_version=fix_version,
        labels=list(fields.labels or []),
        components=[c.name for c in (fields.components or [])],
    )


def pull_issues(
    jira: JIRA,
    jql: str,
    custom_fields: dict[str, str] | None = None,
    max_results: int = 0,
) -> list[Task]:
    """Fetch issues matching a JQL query and convert them to Tasks.

    When *max_results* is 0 (default) all matching issues are retrieved.
    The jira library handles pagination internally via search tokens.
    """
    # maxResults=0 tells the library to auto-paginate and fetch everything.
    # maxResults=False also works but 0 is the documented convention.
    issues = jira.search_issues(jql, maxResults=max_results if max_results > 0 else 0)
    return [issue_to_task(i, custom_fields) for i in issues]


def push_task(jira: JIRA, task: Task, custom_fields: dict[str, str] | None = None) -> None:
    """Push local task changes back to JIRA.

    Only updates fields that are safe to write. Status transitions are
    handled separately because JIRA uses a workflow transition API.
    """
    cf = {**CUSTOM_FIELDS, **(custom_fields or {})}

    update_fields: dict = {
        "summary": task.summary,
        "description": task.description or "",
        "priority": {"name": task.priority} if task.priority else None,
        "labels": task.labels,
    }

    if task.duedate:
        update_fields["duedate"] = task.duedate

    if task.startdate:
        update_fields[cf.get("startdate", "customfield_10015")] = task.startdate

    if task.story_points is not None:
        update_fields[cf["story_points"]] = task.story_points

    if task.fix_version:
        update_fields["fixVersions"] = [{"name": task.fix_version}]

    if task.epic_link:
        update_fields[cf["epic_link"]] = task.epic_link

    if task.components:
        update_fields["components"] = [{"name": c} for c in task.components]

    # Remove None values — JIRA rejects them
    update_fields = {k: v for k, v in update_fields.items() if v is not None}

    issue = jira.issue(task.key)
    issue.update(fields=update_fields)

    # Assignee requires accountId lookup — handle separately
    if task.assignee:
        project = task.key.split("-")[0]
        users = jira.search_assignable_users_for_issues(
            query=task.assignee, project=project, maxResults=1,
        )
        if users:
            issue.update(fields={"assignee": {"accountId": users[0].accountId}})


def transition_task(jira: JIRA, task: Task, target_status: str) -> bool:
    """Attempt to transition an issue to target_status. Returns True on success."""
    transitions = jira.transitions(task.key)
    for t in transitions:
        if t["to"]["name"].lower() == target_status.lower():
            jira.transition_issue(task.key, t["id"])
            return True
    return False


# ── Version / Release management ─────────────────────────────────


def list_versions(jira: JIRA, project: str) -> list:
    """Return all versions for a project."""
    return jira.project_versions(project)


def create_version(
    jira: JIRA,
    project: str,
    name: str,
    description: str = "",
    start_date: str | None = None,
    release_date: str | None = None,
) -> object:
    """Create a new version in the given project."""
    return jira.create_version(
        name=name,
        project=project,
        description=description,
        startDate=start_date,
        releaseDate=release_date,
    )


def release_version(jira: JIRA, project: str, name: str) -> None:
    """Mark a version as released."""
    for v in jira.project_versions(project):
        if v.name == name:
            v.update(released=True)
            return
    raise ValueError(f"Version '{name}' not found in project {project}")


def get_version_issues(
    jira: JIRA,
    project: str,
    version_name: str,
    custom_fields: dict[str, str] | None = None,
) -> list[Task]:
    """Fetch all issues assigned to a specific fixVersion."""
    jql = f'project = {project} AND fixVersion = "{version_name}" ORDER BY key ASC'
    return pull_issues(jira, jql, custom_fields=custom_fields)


def assign_version(jira: JIRA, issue_key: str, version_name: str) -> None:
    """Set the fixVersion on a single issue."""
    issue = jira.issue(issue_key)
    issue.update(fields={"fixVersions": [{"name": version_name}]})


# ── Release notes ────────────────────────────────────────────────

# Issue type → category mapping (handles Spanish + English names)
_TYPE_CATEGORIES = {
    "features": {"story", "historia", "epic"},
    "bug_fixes": {"bug", "error"},
}


def _categorize(issuetype: str) -> str:
    """Map an issue type name to a release-notes category."""
    lower = issuetype.lower()
    for cat, types in _TYPE_CATEGORIES.items():
        if lower in types:
            return cat
    return "tasks"


def generate_release_notes(
    jira: JIRA,
    project: str,
    version_name: str,
    server: str = "",
) -> str:
    """Generate markdown release notes for a version."""
    tasks = get_version_issues(jira, project, version_name)

    groups: dict[str, list[Task]] = {"features": [], "bug_fixes": [], "tasks": []}
    for t in tasks:
        groups[_categorize(t.issuetype)].append(t)

    lines = [f"# Release Notes — {version_name}", ""]

    section_titles = {
        "features": "Features",
        "bug_fixes": "Bug Fixes",
        "tasks": "Tasks",
    }

    for cat in ("features", "bug_fixes", "tasks"):
        items = groups[cat]
        if not items:
            continue
        lines.append(f"## {section_titles[cat]}")
        lines.append("")
        for t in items:
            link = f"{server}/browse/{t.key}" if server else t.key
            status_tag = f" `{t.status}`" if t.status != "Finalizada" else ""
            if server:
                lines.append(f"- **[{t.key}]({link})**: {t.summary}{status_tag}")
            else:
                lines.append(f"- **{t.key}**: {t.summary}{status_tag}")
        lines.append("")

    if not any(groups.values()):
        lines.append("_No issues assigned to this version._")
        lines.append("")

    return "\n".join(lines)
