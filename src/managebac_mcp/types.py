"""Serializable models used across tools/services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ToolArtifacts:
    screenshot: str | None = None
    html: str | None = None


@dataclass
class ToolResult:
    success: bool
    message: str
    error_code: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: ToolArtifacts = field(default_factory=ToolArtifacts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "error_code": self.error_code,
            "data": self.data,
            "artifacts": {
                "screenshot": self.artifacts.screenshot,
                "html": self.artifacts.html,
            },
        }


@dataclass
class ClassRecord:
    class_id: int
    title: str
    teacher: str | None
    url: str
    raw_hash: str


@dataclass
class TaskRecord:
    task_id: int
    class_id: int
    title: str
    due_at: datetime | None
    status: str | None
    url: str
    dropbox_url: str
    raw_hash: str


@dataclass
class CasExperienceRecord:
    experience_id: int
    title: str
    status: str | None
    start_date: str | None
    end_date: str | None
    hours: float | None
    url: str
    raw_hash: str


@dataclass
class ReflectionInput:
    experience_id: int
    outcomes: list[str]


@dataclass
class JournalReflectionInput(ReflectionInput):
    text: str


@dataclass
class FileReflectionInput(ReflectionInput):
    file_path: Path


@dataclass
class LinkReflectionInput(ReflectionInput):
    url: str
