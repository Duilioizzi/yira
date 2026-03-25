"""Local task model — YAML serialization and dirty-tracking."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Task:
    """Represents a single JIRA issue stored locally as YAML."""

    # Identity
    key: str                              # e.g. "PROJ-123"
    issue_id: str = ""                    # JIRA internal ID

    # Core fields
    summary: str = ""
    description: str = ""
    issuetype: str = ""
    status: str = ""
    priority: str = ""
    assignee: str = ""

    # Dates
    duedate: str = ""                     # ISO date YYYY-MM-DD ("Fecha de Vencimiento")
    startdate: str = ""                   # ISO date YYYY-MM-DD (custom field)

    # Agile fields
    story_points: float | None = None
    epic_link: str = ""                   # parent epic key
    epic_name: str = ""                   # only for epic issues
    sprint: str = ""
    fix_version: str = ""                # fixVersion / release name
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)

    # Metadata (local-only, not pushed to JIRA)
    _synced_hash: str = ""                # hash at last pull/push
    _last_synced: str = ""                # ISO timestamp
    _dirty: bool = False                  # computed, not stored

    # ── Serialization ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return dict suitable for YAML dump (excludes computed fields)."""
        d = asdict(self)
        # Remove computed field
        d.pop("_dirty", None)
        return d

    def content_hash(self) -> str:
        """Hash of pushable fields — used to detect local edits."""
        pushable = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        raw = yaml.dump(pushable, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def check_dirty(self) -> bool:
        """Return True if local content differs from last synced state."""
        self._dirty = self._synced_hash != "" and self._synced_hash != self.content_hash()
        return self._dirty

    def mark_synced(self) -> None:
        """Stamp current hash + time after a successful push/pull."""
        self._synced_hash = self.content_hash()
        self._last_synced = datetime.now().isoformat(timespec="seconds")
        self._dirty = False

    # ── File I/O ───────────────────────────────────────────────────

    def save(self, tasks_dir: Path) -> Path:
        """Write task to tasks_dir/<KEY>.yaml. Returns the file path."""
        tasks_dir.mkdir(parents=True, exist_ok=True)
        path = tasks_dir / f"{self.key}.yaml"
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return path

    @classmethod
    def load(cls, path: Path) -> "Task":
        """Load a Task from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        # Pop metadata fields that use underscore prefix
        synced_hash = data.pop("_synced_hash", "")
        last_synced = data.pop("_last_synced", "")
        data.pop("_dirty", None)
        task = cls(**data)
        task._synced_hash = synced_hash
        task._last_synced = last_synced
        task.check_dirty()
        return task

    @classmethod
    def list_local(cls, tasks_dir: Path) -> list["Task"]:
        """Load all tasks from the tasks directory."""
        if not tasks_dir.exists():
            return []
        tasks = []
        for p in sorted(tasks_dir.glob("*.yaml")):
            tasks.append(cls.load(p))
        return tasks
