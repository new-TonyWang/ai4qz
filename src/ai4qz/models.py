from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Defaults:
    cookies_file: Path | None = None
    har_file: Path | None = None
    rows: int = 24
    cols: int = 80
    connect_timeout_sec: int = 10
    command_timeout_sec: int = 60
    concurrency: int = 4


@dataclass(slots=True)
class NotebookTarget:
    name: str
    tags: list[str] = field(default_factory=list)
    base_url: str | None = None
    entry_url: str | None = None
    notebook_id: str | None = None
    cookies_file: Path | None = None
    har_file: Path | None = None
    notes: str | None = None


@dataclass(slots=True)
class ProjectConfig:
    path: Path
    defaults: Defaults
    notebooks: list[NotebookTarget]

    def get_target(self, name: str) -> NotebookTarget:
        for notebook in self.notebooks:
            if notebook.name == name:
                return notebook
        raise KeyError(f"unknown notebook target: {name}")

    def select_targets(
        self,
        *,
        names: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[NotebookTarget]:
        selected = self.notebooks
        if names:
            ordered: list[NotebookTarget] = []
            for name in names:
                ordered.append(self.get_target(name))
            selected = ordered
        if tags:
            selected = [
                notebook
                for notebook in selected
                if all(tag in notebook.tags for tag in tags)
            ]
        return selected


@dataclass(slots=True)
class ResolvedTarget:
    name: str
    base_url: str
    cookies_file: Path
    notebook_id: str | None
    resolved_from: str
    tags: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    base_url: str
    cookies_file: Path
    resolved_from: str
    xsrf_found: bool
    terminal_count: int = 0
    terminal_names: list[str] = field(default_factory=list)
    contents_api_ok: bool = False
    probe_exit_code: int | None = None
    probe_output: str = ""
    error: str | None = None


@dataclass(slots=True)
class CommandResult:
    name: str
    ok: bool
    exit_code: int | None
    output: str
    terminal_name: str | None
    seconds: float
    error: str | None = None
