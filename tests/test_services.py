from __future__ import annotations

from datetime import datetime
from pathlib import Path

from managebac_mcp.browser import UploadOutcome
from managebac_mcp.db import Database
from managebac_mcp.services import ActionService, ReadService, SyncService
from managebac_mcp.types import CasExperienceRecord, ClassRecord, TaskRecord


class FakeBrowser:
    def login(self, username: str, password: str) -> None:
        assert username
        assert password

    def fetch_classes(self):
        return [
            ClassRecord(class_id=12816550, title="Math", teacher="A", url="https://x/classes/12816550", raw_hash="h1"),
            ClassRecord(class_id=12816551, title="Biology", teacher="B", url="https://x/classes/12816551", raw_hash="h2"),
        ]

    def fetch_tasks(self, class_id: int):
        return [
            TaskRecord(
                task_id=47417931 + class_id,
                class_id=class_id,
                title=f"Task for {class_id}",
                due_at=datetime(2026, 3, 10, 10, 0, 0),
                status="open",
                url=f"https://x/student/classes/{class_id}/core_tasks/{47417931 + class_id}",
                dropbox_url=f"https://x/student/classes/{class_id}/core_tasks/{47417931 + class_id}/dropbox",
                raw_hash="t1",
            )
        ]

    def fetch_cas_experiences(self):
        return [
            CasExperienceRecord(
                experience_id=26331638,
                title="CAS Project",
                status="ongoing",
                start_date="2026-01-01",
                end_date="2026-06-01",
                hours=20.0,
                url="https://x/student/ib/activity/cas/26331638",
                raw_hash="c1",
            )
        ]

    def submit_task_file(self, task_dropbox_url: str, file_path: Path, comment: str | None = None):
        return UploadOutcome(status="submitted", message="ok", screenshot_path="artifacts/a.png", html_path="artifacts/a.html")

    def create_cas_experience(self, payload: dict):
        return {"status": "ok", "payload": payload}

    def add_cas_reflection_journal(self, experience_id: int, text: str, outcomes: list[str]):
        return {"status": "ok", "experience_id": experience_id}

    def add_cas_reflection_file(self, experience_id: int, file_path: Path, outcomes: list[str]):
        return {"status": "ok", "experience_id": experience_id}

    def add_cas_reflection_link(self, experience_id: int, reflection_type: str, url: str, outcomes: list[str]):
        return {"status": "ok", "experience_id": experience_id, "type": reflection_type}


def build_db() -> Database:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    return db


def test_startup_sync_and_reads(tmp_path: Path):
    db = build_db()
    browser = FakeBrowser()
    sync = SyncService(db, browser)
    read = ReadService(db)

    res = sync.run_startup_sync()
    assert res.success
    assert res.data["classes"] == 2

    classes = read.list_classes()
    assert classes.success
    assert len(classes.data["classes"]) == 2

    tasks = read.class_tasks(12816550)
    assert tasks.success
    assert len(tasks.data["tasks"]) == 1

    cas = read.cas_dashboard()
    assert cas.success
    assert len(cas.data["experiences"]) == 1


def test_submit_and_read_submission(tmp_path: Path):
    db = build_db()
    browser = FakeBrowser()
    sync = SyncService(db, browser)
    sync.run_startup_sync()

    action = ActionService(db, browser)
    read = ReadService(db)

    file_path = tmp_path / "report.txt"
    file_path.write_text("hello", encoding="utf-8")

    task_id = 47417931 + 12816550
    submit = action.submit_task_file(task_id=task_id, file_path=str(file_path))
    assert submit.success

    result = read.submission_result(task_id)
    assert result.success
    assert result.data["submission"]["file_name"] == "report.txt"


def test_cas_reflections_actions(tmp_path: Path):
    db = build_db()
    browser = FakeBrowser()
    sync = SyncService(db, browser)
    sync.run_startup_sync()

    action = ActionService(db, browser)
    read = ReadService(db)

    experience_id = 26331638
    j = action.add_reflection_journal(experience_id, "My reflection", ["Awareness"])
    assert j.success

    fpath = tmp_path / "evidence.pdf"
    fpath.write_text("pdf", encoding="utf-8")
    f = action.add_reflection_file(experience_id, str(fpath), ["Ethics"])
    assert f.success

    v = action.add_reflection_link(experience_id, "video", "https://youtu.be/example", ["Global Value"])
    assert v.success

    rows = read.cas_reflections(experience_id)
    assert rows.success
    assert len(rows.data["reflections"]) == 3
