"""Application services for sync/read/actions."""

from __future__ import annotations

from pathlib import Path

from .browser import BrowserGateway
from .db import Database
from .errors import AppError, CAS_EXPERIENCE_NOT_FOUND, CLASS_NOT_FOUND, TASK_NOT_FOUND
from .repositories import CasRepository, ClassRepository, SnapshotRepository, SubmissionRepository, SyncRunRepository, TaskRepository
from .types import ToolArtifacts, ToolResult


class SyncService:
    def __init__(self, db: Database, browser: BrowserGateway):
        self.db = db
        self.browser = browser

    def run_startup_sync(self) -> ToolResult:
        with self.db.session() as session:
            sync_repo = SyncRunRepository(session)
            classes_repo = ClassRepository(session)
            task_repo = TaskRepository(session)
            cas_repo = CasRepository(session)

            run = sync_repo.start()
            try:
                classes = self.browser.fetch_classes()
                classes_repo.upsert_many(classes)

                total_tasks = 0
                for cls in classes:
                    tasks = self.browser.fetch_tasks(cls.class_id)
                    total_tasks += task_repo.upsert_many(tasks)

                experiences = self.browser.fetch_cas_experiences()
                cas_repo.upsert_experiences(experiences)
                sync_repo.finish(run, "success")
                return ToolResult(
                    success=True,
                    message="Startup sync completed",
                    data={
                        "classes": len(classes),
                        "tasks": total_tasks,
                        "cas_experiences": len(experiences),
                        "sync_run_id": run.id,
                    },
                )
            except AppError as exc:
                sync_repo.finish(run, "failed", error_code=exc.code, error_message=exc.message)
                return ToolResult(success=False, message=exc.message, error_code=exc.code, data={"sync_run_id": run.id})


class ReadService:
    def __init__(self, db: Database):
        self.db = db

    def auth_status(self) -> ToolResult:
        return ToolResult(success=True, message="Auth status can be validated by running action_login", data={})

    def list_classes(self) -> ToolResult:
        with self.db.session() as session:
            classes = ClassRepository(session).list_all()
            return ToolResult(
                success=True,
                message=f"Returned {len(classes)} classes",
                data={
                    "classes": [
                        {
                            "class_id": c.class_id,
                            "title": c.title,
                            "teacher": c.teacher,
                            "url": c.url,
                            "last_seen_at": c.last_seen_at.isoformat(),
                        }
                        for c in classes
                    ]
                },
            )

    def class_details(self, class_id: int) -> ToolResult:
        with self.db.session() as session:
            repo = ClassRepository(session)
            cls = repo.get(class_id)
            if cls is None:
                return ToolResult(success=False, message=f"Class {class_id} not found", error_code=CLASS_NOT_FOUND)
            return ToolResult(
                success=True,
                message="Class found",
                data={
                    "class": {
                        "class_id": cls.class_id,
                        "title": cls.title,
                        "teacher": cls.teacher,
                        "url": cls.url,
                    }
                },
            )

    def class_tasks(self, class_id: int) -> ToolResult:
        with self.db.session() as session:
            classes_repo = ClassRepository(session)
            task_repo = TaskRepository(session)
            if classes_repo.get(class_id) is None:
                return ToolResult(success=False, message=f"Class {class_id} not found", error_code=CLASS_NOT_FOUND)
            tasks = task_repo.list_by_class(class_id)
            return ToolResult(
                success=True,
                message=f"Returned {len(tasks)} tasks",
                data={
                    "tasks": [
                        {
                            "task_id": t.task_id,
                            "title": t.title,
                            "status": t.status,
                            "due_at": t.due_at.isoformat() if t.due_at else None,
                            "url": t.url,
                            "dropbox_url": t.dropbox_url,
                        }
                        for t in tasks
                    ]
                },
            )

    def task_details(self, task_id: int) -> ToolResult:
        with self.db.session() as session:
            task = TaskRepository(session).get(task_id)
            if task is None:
                return ToolResult(success=False, message=f"Task {task_id} not found", error_code=TASK_NOT_FOUND)
            return ToolResult(
                success=True,
                message="Task found",
                data={
                    "task": {
                        "task_id": task.task_id,
                        "class_id": task.class_id,
                        "title": task.title,
                        "status": task.status,
                        "due_at": task.due_at.isoformat() if task.due_at else None,
                        "url": task.url,
                        "dropbox_url": task.dropbox_url,
                    }
                },
            )

    def task_dropbox(self, task_id: int) -> ToolResult:
        with self.db.session() as session:
            task = TaskRepository(session).get(task_id)
            if task is None:
                return ToolResult(success=False, message=f"Task {task_id} not found", error_code=TASK_NOT_FOUND)
            return ToolResult(success=True, message="Dropbox URL available", data={"dropbox_url": task.dropbox_url})

    def submission_result(self, task_id: int) -> ToolResult:
        with self.db.session() as session:
            task = TaskRepository(session).get(task_id)
            if task is None:
                return ToolResult(success=False, message=f"Task {task_id} not found", error_code=TASK_NOT_FOUND)
            sub = SubmissionRepository(session).last_for_task(task_id)
            if sub is None:
                return ToolResult(success=True, message="No submissions recorded", data={"submission": None})
            return ToolResult(
                success=True,
                message="Latest submission found",
                data={
                    "submission": {
                        "submitted_at": sub.submitted_at.isoformat(),
                        "file_name": sub.file_name,
                        "result_status": sub.result_status,
                        "message": sub.message,
                        "artifact_path": sub.artifact_path,
                    }
                },
            )

    def cas_dashboard(self) -> ToolResult:
        with self.db.session() as session:
            experiences = CasRepository(session).list_experiences()
            return ToolResult(
                success=True,
                message=f"Returned {len(experiences)} CAS experiences",
                data={
                    "experiences": [
                        {
                            "experience_id": e.experience_id,
                            "title": e.title,
                            "status": e.status,
                            "start_date": e.start_date,
                            "end_date": e.end_date,
                            "hours": e.hours,
                            "url": e.url,
                        }
                        for e in experiences
                    ]
                },
            )

    def cas_experience(self, experience_id: int) -> ToolResult:
        with self.db.session() as session:
            row = CasRepository(session).get_experience(experience_id)
            if row is None:
                return ToolResult(
                    success=False,
                    message=f"CAS experience {experience_id} not found",
                    error_code=CAS_EXPERIENCE_NOT_FOUND,
                )
            return ToolResult(
                success=True,
                message="CAS experience found",
                data={
                    "experience": {
                        "experience_id": row.experience_id,
                        "title": row.title,
                        "status": row.status,
                        "start_date": row.start_date,
                        "end_date": row.end_date,
                        "hours": row.hours,
                        "url": row.url,
                    }
                },
            )

    def cas_reflections(self, experience_id: int) -> ToolResult:
        with self.db.session() as session:
            repo = CasRepository(session)
            if repo.get_experience(experience_id) is None:
                return ToolResult(
                    success=False,
                    message=f"CAS experience {experience_id} not found",
                    error_code=CAS_EXPERIENCE_NOT_FOUND,
                )
            rows = repo.list_reflections(experience_id)
            return ToolResult(
                success=True,
                message=f"Returned {len(rows)} reflections",
                data={
                    "reflections": [
                        {
                            "id": r.id,
                            "reflection_id": r.reflection_id,
                            "type": r.type,
                            "content_preview": r.content_preview,
                            "url": r.url,
                            "created_at": r.created_at.isoformat(),
                        }
                        for r in rows
                    ]
                },
            )


class ActionService:
    def __init__(self, db: Database, browser: BrowserGateway):
        self.db = db
        self.browser = browser

    def login(self, username: str, password: str) -> ToolResult:
        self.browser.login(username, password)
        return ToolResult(success=True, message="Login successful")

    def refresh_classes(self) -> ToolResult:
        with self.db.session() as session:
            classes = self.browser.fetch_classes()
            count = ClassRepository(session).upsert_many(classes)
            return ToolResult(success=True, message=f"Refreshed {count} classes", data={"classes": count})

    def refresh_class_tasks(self, class_id: int) -> ToolResult:
        with self.db.session() as session:
            repo = ClassRepository(session)
            if repo.get(class_id) is None:
                return ToolResult(success=False, message=f"Class {class_id} not found", error_code=CLASS_NOT_FOUND)
            tasks = self.browser.fetch_tasks(class_id)
            count = TaskRepository(session).upsert_many(tasks)
            return ToolResult(success=True, message=f"Refreshed {count} tasks", data={"tasks": count, "class_id": class_id})

    def submit_task_file(self, task_id: int, file_path: str, comment: str | None = None) -> ToolResult:
        p = Path(file_path)
        with self.db.session() as session:
            task_repo = TaskRepository(session)
            sub_repo = SubmissionRepository(session)
            task = task_repo.get(task_id)
            if task is None:
                return ToolResult(success=False, message=f"Task {task_id} not found", error_code=TASK_NOT_FOUND)
            outcome = self.browser.submit_task_file(task.dropbox_url, p, comment=comment)
            sub_repo.create(task_id=task_id, file_name=p.name, result_status=outcome.status, message=outcome.message, artifact_path=outcome.screenshot_path)
            SnapshotRepository(session).create(
                page_type="task_dropbox",
                entity_id=str(task_id),
                html_path=outcome.html_path,
                screenshot_path=outcome.screenshot_path,
            )
            return ToolResult(
                success=True,
                message="File submitted",
                data={"task_id": task_id, "status": outcome.status, "status_message": outcome.message},
                artifacts=ToolArtifacts(screenshot=outcome.screenshot_path, html=outcome.html_path),
            )

    def retry_submission(self, task_id: int, file_path: str) -> ToolResult:
        return self.submit_task_file(task_id=task_id, file_path=file_path)

    def refresh_cas(self) -> ToolResult:
        with self.db.session() as session:
            experiences = self.browser.fetch_cas_experiences()
            count = CasRepository(session).upsert_experiences(experiences)
            return ToolResult(success=True, message=f"Refreshed {count} CAS experiences", data={"experiences": count})

    def create_cas_experience(self, payload: dict) -> ToolResult:
        data = self.browser.create_cas_experience(payload)
        return ToolResult(success=True, message="CAS experience action executed", data=data)

    def add_reflection_journal(self, experience_id: int, text: str, outcomes: list[str]) -> ToolResult:
        with self.db.session() as session:
            repo = CasRepository(session)
            if repo.get_experience(experience_id) is None:
                return ToolResult(success=False, message=f"CAS experience {experience_id} not found", error_code=CAS_EXPERIENCE_NOT_FOUND)
            data = self.browser.add_cas_reflection_journal(experience_id, text, outcomes)
            repo.create_reflection(experience_id, "journal", text[:200], data.get("html"))
            return ToolResult(success=True, message="CAS journal reflection submitted", data=data)

    def add_reflection_file(self, experience_id: int, file_path: str, outcomes: list[str]) -> ToolResult:
        with self.db.session() as session:
            repo = CasRepository(session)
            if repo.get_experience(experience_id) is None:
                return ToolResult(success=False, message=f"CAS experience {experience_id} not found", error_code=CAS_EXPERIENCE_NOT_FOUND)
            data = self.browser.add_cas_reflection_file(experience_id, Path(file_path), outcomes)
            repo.create_reflection(experience_id, "file", Path(file_path).name, data.get("html"))
            return ToolResult(success=True, message="CAS file reflection submitted", data=data)

    def add_reflection_link(self, experience_id: int, reflection_type: str, url: str, outcomes: list[str]) -> ToolResult:
        with self.db.session() as session:
            repo = CasRepository(session)
            if repo.get_experience(experience_id) is None:
                return ToolResult(success=False, message=f"CAS experience {experience_id} not found", error_code=CAS_EXPERIENCE_NOT_FOUND)
            data = self.browser.add_cas_reflection_link(experience_id, reflection_type, url, outcomes)
            repo.create_reflection(experience_id, reflection_type, url[:200], data.get("html"))
            return ToolResult(success=True, message=f"CAS {reflection_type} reflection submitted", data=data)
