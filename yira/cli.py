"""CLI entry-point — local-first JIRA task management."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .client import (
    CUSTOM_FIELDS,
    connect, issue_to_task, pull_issues, push_task, transition_task,
    list_versions, create_version, release_version,
    get_version_issues, assign_version, generate_release_notes,
)
from .config import load_config
from .models import Task

console = Console()


def _resolve_tasks_dir(cfg) -> Path:
    """Return absolute tasks dir (relative paths resolved from config location)."""
    return Path(cfg.tasks_dir).resolve()


# ── Root group ──────────────────────────────────────────────────────

@click.group()
@click.option("--config", "config_path", type=click.Path(exists=True), default=None, help="Path to config.yaml")
@click.pass_context
def cli(ctx, config_path):
    """yira — local-first JIRA task manager."""
    ctx.ensure_object(dict)
    cfg = load_config(Path(config_path) if config_path else None)
    ctx.obj["cfg"] = cfg
    ctx.obj["tasks_dir"] = _resolve_tasks_dir(cfg)


# ── pull ────────────────────────────────────────────────────────────

@cli.command()
@click.option("--jql", default=None, help="Custom JQL query")
@click.option("--project", default=None, help="JIRA project key (overrides config)")
@click.option("--sprint", "current_sprint", is_flag=True, help="Only current sprint")
@click.option("--assignee", default=None, help="Filter by assignee (use 'me' for yourself)")
@click.option("--max", "max_results", default=0, help="Max issues to fetch (0 = all)")
@click.pass_context
def pull(ctx, jql, project, current_sprint, assignee, max_results):
    """Pull issues from JIRA and save as local YAML files."""
    cfg = ctx.obj["cfg"]
    tasks_dir = ctx.obj["tasks_dir"]

    if not jql:
        proj = project or cfg.jira.default_project
        if not proj:
            console.print("[red]Provide --project or set default_project in config.yaml[/red]")
            raise SystemExit(1)
        parts = [f"project = {proj}"]
        if current_sprint:
            parts.append("sprint in openSprints()")
        if assignee:
            parts.append(f"assignee = {assignee}")
        jql = " AND ".join(parts) + " ORDER BY key DESC"

    console.print(f"[dim]JQL: {jql}[/dim]")

    jira = connect(cfg)
    tasks = pull_issues(jira, jql, max_results=max_results)

    for task in tasks:
        task.mark_synced()
        path = task.save(tasks_dir)
        console.print(f"  [green]↓[/green] {task.key}  {task.summary[:60]}")

    console.print(f"\n[bold]Pulled {len(tasks)} issues → {tasks_dir}/[/bold]")


# ── push ────────────────────────────────────────────────────────────

@cli.command()
@click.argument("keys", nargs=-1)
@click.option("--dirty", is_flag=True, help="Push only locally modified tasks")
@click.option("--dry-run", is_flag=True, help="Show what would be pushed without sending")
@click.option("--transition/--no-transition", default=False, help="Also transition status")
@click.pass_context
def push(ctx, keys, dirty, dry_run, transition):
    """Push local changes to JIRA.

    Specify task keys (e.g. PROJ-1 PROJ-2) or use --dirty to push all
    locally modified tasks. Without arguments, nothing is pushed — this is
    intentional to avoid accidental bulk updates.
    """
    cfg = ctx.obj["cfg"]
    tasks_dir = ctx.obj["tasks_dir"]
    all_tasks = Task.list_local(tasks_dir)

    # Select tasks to process
    if keys:
        selected = [t for t in all_tasks if t.key in keys]
        missing = set(keys) - {t.key for t in selected}
        if missing:
            console.print(f"[yellow]Not found locally: {', '.join(missing)}[/yellow]")
    elif dirty:
        selected = [t for t in all_tasks if t.check_dirty()]
    else:
        console.print("[yellow]Specify task keys or use --dirty to push modified tasks.[/yellow]")
        console.print("Run [bold]yira list --dirty[/bold] to see what has changed.")
        return

    if not selected:
        console.print("[dim]Nothing to push.[/dim]")
        return

    if dry_run:
        console.print("[bold]Dry run — these would be pushed:[/bold]")
        for t in selected:
            console.print(f"  {t.key}  {t.summary[:60]}")
        return

    jira = connect(cfg)
    for task in selected:
        try:
            push_task(jira, task)
            if transition:
                transition_task(jira, task, task.status)
            task.mark_synced()
            task.save(tasks_dir)
            console.print(f"  [green]↑[/green] {task.key}  {task.summary[:60]}")
        except Exception as e:
            console.print(f"  [red]✗[/red] {task.key}  {e}")

    console.print(f"\n[bold]Pushed {len(selected)} task(s).[/bold]")


# ── list ────────────────────────────────────────────────────────────

@cli.command(name="list")
@click.option("--dirty", is_flag=True, help="Show only locally modified tasks")
@click.option("--status", default=None, help="Filter by status")
@click.option("--assignee", default=None, help="Filter by assignee")
@click.option("--epic", default=None, help="Filter by epic key")
@click.pass_context
def list_tasks(ctx, dirty, status, assignee, epic):
    """List local tasks."""
    tasks_dir = ctx.obj["tasks_dir"]
    tasks = Task.list_local(tasks_dir)

    if dirty:
        tasks = [t for t in tasks if t.check_dirty()]
    if status:
        tasks = [t for t in tasks if status.lower() in t.status.lower()]
    if assignee:
        tasks = [t for t in tasks if assignee.lower() in t.assignee.lower()]
    if epic:
        tasks = [t for t in tasks if t.epic_link == epic]

    if not tasks:
        console.print("[dim]No matching tasks.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Type", width=10)
    table.add_column("Status", width=14)
    table.add_column("SP", width=4, justify="right")
    table.add_column("Assignee", width=16)
    table.add_column("Summary")
    table.add_column("Dirty", width=5, justify="center")

    for t in tasks:
        t.check_dirty()
        table.add_row(
            t.key,
            t.issuetype,
            t.status,
            str(t.story_points or ""),
            t.assignee[:16],
            t.summary[:50],
            "[red]●[/red]" if t._dirty else "",
        )

    console.print(table)
    console.print(f"[dim]{len(tasks)} task(s)[/dim]")


# ── create ──────────────────────────────────────────────────────────

@cli.command()
@click.option("--project", default=None, help="JIRA project key")
@click.option("--type", "issuetype", default="Task", help="Issue type (Task, Story, Bug, Epic)")
@click.option("--summary", "-s", required=True, help="Issue summary / title")
@click.option("--description", "-d", default="", help="Issue description")
@click.option("--assignee", "-a", default="", help="Assignee display name")
@click.option("--priority", "-p", default="Medium", help="Priority")
@click.option("--points", type=float, default=None, help="Story points")
@click.option("--epic", default="", help="Parent epic key")
@click.option("--labels", default="", help="Comma-separated labels")
@click.option("--push-now", is_flag=True, help="Create on JIRA immediately")
@click.pass_context
def create(ctx, project, issuetype, summary, description, assignee, priority, points, epic, labels, push_now):
    """Create a new task locally (optionally push to JIRA right away)."""
    cfg = ctx.obj["cfg"]
    tasks_dir = ctx.obj["tasks_dir"]
    proj = project or cfg.jira.default_project

    if not proj:
        console.print("[red]Provide --project or set default_project in config.yaml[/red]")
        raise SystemExit(1)

    if push_now:
        from .client import CUSTOM_FIELDS
        jira = connect(cfg)
        fields = {
            "project": {"key": proj},
            "summary": summary,
            "description": description or "",
            "issuetype": {"name": issuetype},
            "priority": {"name": priority},
        }
        if labels:
            fields["labels"] = [l.strip() for l in labels.split(",")]
        if epic:
            fields[CUSTOM_FIELDS["epic_link"]] = epic
        if points is not None:
            fields[CUSTOM_FIELDS["story_points"]] = points
        issue = jira.create_issue(fields=fields)
        # Assign after creation (requires accountId lookup)
        if assignee:
            try:
                users = jira.search_users(query=assignee, maxResults=1)
                if users:
                    jira.assign_issue(issue.key, users[0].accountId)
            except Exception:
                console.print(f"[yellow]Warning: could not assign to '{assignee}'[/yellow]")
        task = issue_to_task(issue)
        task.mark_synced()
        path = task.save(tasks_dir)
        console.print(f"[green]Created {task.key}[/green] → {path}")
    else:
        # Local-only: use a placeholder key until pushed
        existing = list(tasks_dir.glob(f"NEW-*.yaml"))
        seq = len(existing) + 1
        key = f"NEW-{seq:04d}"
        task = Task(
            key=key,
            summary=summary,
            description=description,
            issuetype=issuetype,
            assignee=assignee,
            priority=priority,
            story_points=points,
            epic_link=epic,
            labels=[l.strip() for l in labels.split(",")] if labels else [],
        )
        path = task.save(tasks_dir)
        console.print(f"[green]Created locally[/green] {key} → {path}")
        console.print("[dim]Use 'yira push-new' to create these on JIRA.[/dim]")


# ── push-new ────────────────────────────────────────────────────────

@cli.command(name="push-new")
@click.option("--project", default=None, help="JIRA project key")
@click.pass_context
def push_new(ctx, project):
    """Push locally-created tasks (NEW-*) to JIRA and rename to real keys."""
    cfg = ctx.obj["cfg"]
    tasks_dir = ctx.obj["tasks_dir"]
    proj = project or cfg.jira.default_project

    if not proj:
        console.print("[red]Provide --project or set default_project in config.yaml[/red]")
        raise SystemExit(1)

    new_files = sorted(tasks_dir.glob("NEW-*.yaml"))
    if not new_files:
        console.print("[dim]No new local tasks to push.[/dim]")
        return

    jira = connect(cfg)
    cf = CUSTOM_FIELDS
    for path in new_files:
        task = Task.load(path)
        fields = {
            "project": {"key": proj},
            "summary": task.summary,
            "description": task.description or "",
            "issuetype": {"name": task.issuetype or "Task"},
            "priority": {"name": task.priority or "Medium"},
        }
        if task.assignee:
            # Cloud uses accountId; search assignable users to resolve display name
            users = jira.search_assignable_users_for_issues(
                query=task.assignee, project=proj, maxResults=1,
            )
            if users:
                fields["assignee"] = {"accountId": users[0].accountId}
        if task.labels:
            fields["labels"] = task.labels
        if task.story_points is not None:
            fields[cf["story_points"]] = task.story_points
        if task.epic_link:
            fields[cf["epic_link"]] = task.epic_link
        if task.sprint:
            # Sprint requires board lookup; assign after creation
            pass
        if task.components:
            fields["components"] = [{"name": c} for c in task.components]
        if task.duedate:
            fields["duedate"] = task.duedate
        if task.startdate:
            fields[cf.get("startdate", "customfield_10015")] = task.startdate
        if task.fix_version:
            fields["fixVersions"] = [{"name": task.fix_version}]

        try:
            issue = jira.create_issue(fields=fields)
            # Assign to sprint after creation (requires board lookup)
            if task.sprint:
                boards = jira.boards(projectKeyOrID=proj, maxResults=5)
                for board in boards:
                    sprints = jira.sprints(board.id, state="active,future")
                    for s in sprints:
                        if s.name == task.sprint:
                            jira.add_issues_to_sprint(s.id, [issue.key])
                            break
                    else:
                        continue
                    break
            new_task = issue_to_task(jira.issue(issue.key))
            new_task.mark_synced()
            new_task.save(tasks_dir)
            path.unlink()  # remove NEW-XXXX.yaml
            console.print(f"  [green]↑[/green] {task.key} → {new_task.key}  {new_task.summary[:60]}")
        except Exception as e:
            console.print(f"  [red]✗[/red] {task.key}  {e}")


# ── diff ────────────────────────────────────────────────────────────

@cli.command()
@click.argument("key")
@click.pass_context
def diff(ctx, key):
    """Show differences between local task and JIRA for a specific key."""
    cfg = ctx.obj["cfg"]
    tasks_dir = ctx.obj["tasks_dir"]
    path = tasks_dir / f"{key}.yaml"

    if not path.exists():
        console.print(f"[red]Local file not found: {path}[/red]")
        raise SystemExit(1)

    local = Task.load(path)
    jira = connect(cfg)
    remote_task = issue_to_task(jira.issue(key))

    fields = ["summary", "description", "status", "priority", "assignee",
              "story_points", "epic_link", "fix_version", "labels", "components", "sprint"]

    has_diff = False
    for field in fields:
        lval = getattr(local, field)
        rval = getattr(remote_task, field)
        if lval != rval:
            has_diff = True
            console.print(f"[bold]{field}[/bold]:")
            console.print(f"  [red]remote:[/red] {rval}")
            console.print(f"  [green]local: [/green] {lval}")

    if not has_diff:
        console.print(f"[dim]{key} is in sync.[/dim]")


# ── release (subcommand group) ─────────────────────────────────────

@cli.group()
@click.pass_context
def release(ctx):
    """Manage JIRA project versions / releases."""
    pass


@release.command(name="list")
@click.option("--project", default=None, help="JIRA project key")
@click.pass_context
def release_list(ctx, project):
    """List all versions in the project."""
    cfg = ctx.obj["cfg"]
    proj = project or cfg.jira.default_project
    if not proj:
        console.print("[red]Provide --project or set default_project in config.yaml[/red]")
        raise SystemExit(1)

    jira = connect(cfg)
    versions = list_versions(jira, proj)

    if not versions:
        console.print("[dim]No versions found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Status", width=12)
    table.add_column("Start Date", width=12)
    table.add_column("Release Date", width=12)
    table.add_column("Description")

    for v in versions:
        table.add_row(
            v.name,
            "[green]Released[/green]" if getattr(v, "released", False) else "Unreleased",
            getattr(v, "startDate", "") or "",
            getattr(v, "releaseDate", "") or "",
            (getattr(v, "description", "") or "")[:50],
        )

    console.print(table)
    console.print(f"[dim]{len(versions)} version(s)[/dim]")


@release.command(name="create")
@click.argument("name")
@click.option("--project", default=None, help="JIRA project key")
@click.option("--description", "-d", default="", help="Version description")
@click.option("--start-date", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--release-date", default=None, help="Release date (YYYY-MM-DD)")
@click.pass_context
def release_create(ctx, name, project, description, start_date, release_date):
    """Create a new JIRA version."""
    cfg = ctx.obj["cfg"]
    proj = project or cfg.jira.default_project
    if not proj:
        console.print("[red]Provide --project or set default_project in config.yaml[/red]")
        raise SystemExit(1)

    jira = connect(cfg)
    try:
        v = create_version(jira, proj, name, description, start_date, release_date)
        console.print(f"[green]Created version:[/green] {v.name}")
    except Exception as e:
        console.print(f"[red]Failed to create version:[/red] {e}")
        raise SystemExit(1)


@release.command(name="status")
@click.argument("name")
@click.option("--project", default=None, help="JIRA project key")
@click.pass_context
def release_status(ctx, name, project):
    """Show tasks in a version grouped by status."""
    cfg = ctx.obj["cfg"]
    proj = project or cfg.jira.default_project
    if not proj:
        console.print("[red]Provide --project or set default_project in config.yaml[/red]")
        raise SystemExit(1)

    jira = connect(cfg)
    tasks = get_version_issues(jira, proj, name)

    if not tasks:
        console.print(f"[dim]No issues in version '{name}'.[/dim]")
        return

    # Group by status
    groups: dict[str, list[Task]] = {}
    for t in tasks:
        groups.setdefault(t.status, []).append(t)

    total_sp = sum(t.story_points or 0 for t in tasks)
    done_sp = sum(t.story_points or 0 for t in tasks if t.status.lower() in ("finalizada", "done"))

    console.print(f"\n[bold]Version: {name}[/bold]  —  {len(tasks)} issues, {done_sp:.0f}/{total_sp:.0f} SP")
    console.print("")

    for status_name, items in groups.items():
        sp = sum(t.story_points or 0 for t in items)
        console.print(f"  [bold]{status_name}[/bold] ({len(items)} issues, {sp:.0f} SP)")
        for t in items:
            pts = f" [{t.story_points:.0f}]" if t.story_points else ""
            console.print(f"    {t.key}  {t.summary[:55]}{pts}")
        console.print("")


@release.command(name="assign")
@click.argument("version")
@click.argument("keys", nargs=-1, required=True)
@click.option("--project", default=None, help="JIRA project key")
@click.pass_context
def release_assign(ctx, version, keys, project):
    """Assign tasks to a version (fixVersion)."""
    cfg = ctx.obj["cfg"]
    tasks_dir = ctx.obj["tasks_dir"]

    jira = connect(cfg)
    for key in keys:
        try:
            assign_version(jira, key, version)
            # Update local YAML if it exists
            path = tasks_dir / f"{key}.yaml"
            if path.exists():
                task = Task.load(path)
                task.fix_version = version
                task.mark_synced()
                task.save(tasks_dir)
            console.print(f"  [green]✓[/green] {key} → {version}")
        except Exception as e:
            console.print(f"  [red]✗[/red] {key}  {e}")


@release.command(name="notes")
@click.argument("name")
@click.option("--project", default=None, help="JIRA project key")
@click.option("--output", "-o", default=None, type=click.Path(), help="Write to file")
@click.pass_context
def release_notes(ctx, name, project, output):
    """Generate markdown release notes for a version."""
    cfg = ctx.obj["cfg"]
    proj = project or cfg.jira.default_project
    if not proj:
        console.print("[red]Provide --project or set default_project in config.yaml[/red]")
        raise SystemExit(1)

    jira = connect(cfg)
    md = generate_release_notes(jira, proj, name, server=cfg.jira.server)

    if output:
        Path(output).write_text(md)
        console.print(f"[green]Release notes written to {output}[/green]")
    else:
        console.print(md)


@release.command(name="ship")
@click.argument("name")
@click.option("--project", default=None, help="JIRA project key")
@click.option("--notes", "-n", default=None, type=click.Path(), help="Also write release notes to file")
@click.pass_context
def release_ship(ctx, name, project, notes):
    """Mark a version as released and optionally generate notes."""
    cfg = ctx.obj["cfg"]
    proj = project or cfg.jira.default_project
    if not proj:
        console.print("[red]Provide --project or set default_project in config.yaml[/red]")
        raise SystemExit(1)

    jira = connect(cfg)
    try:
        release_version(jira, proj, name)
        console.print(f"[green]Version '{name}' marked as released.[/green]")
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        raise SystemExit(1)

    if notes:
        md = generate_release_notes(jira, proj, name, server=cfg.jira.server)
        Path(notes).write_text(md)
        console.print(f"[green]Release notes written to {notes}[/green]")
