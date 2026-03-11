"""Domain errors and stable error codes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


AUTH_FAILED = "AUTH_FAILED"
ROUTE_MISCONFIGURED = "ROUTE_MISCONFIGURED"
CLASS_NOT_FOUND = "CLASS_NOT_FOUND"
TASK_NOT_FOUND = "TASK_NOT_FOUND"
DROPBOX_NOT_AVAILABLE = "DROPBOX_NOT_AVAILABLE"
FILE_NOT_FOUND = "FILE_NOT_FOUND"
UPLOAD_FAILED = "UPLOAD_FAILED"
SUBMIT_FAILED = "SUBMIT_FAILED"
CAS_EXPERIENCE_NOT_FOUND = "CAS_EXPERIENCE_NOT_FOUND"
CAS_REFLECTION_FAILED = "CAS_REFLECTION_FAILED"
UNKNOWN_UI_CHANGE = "UNKNOWN_UI_CHANGE"
DB_ERROR = "DB_ERROR"
