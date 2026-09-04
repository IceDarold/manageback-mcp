"""Application services for sync/read/actions."""

from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta
from pathlib import Path

from .browser import BrowserGateway
from .db import Database
from .errors import AppError, CAS_EXPERIENCE_NOT_FOUND, CLASS_NOT_FOUND, INVALID_INPUT, TASK_NOT_FOUND
from .repositories import CasRepository, ClassRepository, LessonRepository, SnapshotRepository, SubmissionRepository, SyncRunRepository, TaskRepository
from .types import ToolArtifacts, ToolResult


def _relative_due(due: datetime | None, now: datetime) -> str | None:
    """Human phrasing for a deadline relative to now, e.g. "in 3 days" / "overdue by 2 hours"."""
    if due is None:
        return None
    total = (due - now).total_seconds()
    overdue = total < 0
    minutes = abs(total) / 60
    if minutes < 60:
        n = max(1, int(round(minutes)))
        unit = f"{n} minute{'s' if n != 1 else ''}"
    elif minutes < 60 * 24:
        n = int(round(minutes / 60))
        unit = f"{n} hour{'s' if n != 1 else ''}"
    else:
        n = int(round(minutes / 60 / 24))
        unit = f"{n} day{'s' if n != 1 else ''}"
    return f"overdue by {unit}" if overdue else f"in {unit}"


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
            lesson_repo = LessonRepository(session)

            run = sync_repo.start()
            try:
                # One browser login for the whole sync: classes, their tasks,
                # and CAS are scraped in a single authenticated session.
                classes, tasks_by_class, experiences, lessons = self.browser.collect_startup_data()
                classes_repo.upsert_many(classes)

                total_tasks = 0
                for cls in classes:
                    total_tasks += task_repo.upsert_many(tasks_by_class.get(cls.class_id, []))

                cas_repo.upsert_experiences(experiences)
                lesson_repo.upsert_many(lessons)
                sync_repo.finish(run, "success")
                return ToolResult(
                    success=True,
                    message="Startup sync completed",
                    data={
                        "classes": len(classes),
                        "tasks": total_tasks,
                        "cas_experiences": len(experiences),
                        "lessons": len(lessons),
                        "sync_run_id": run.id,
                    },
                )
            except AppError as exc:
                sync_repo.finish(run, "failed", error_code=exc.code, error_message=exc.message)
                return ToolResult(success=False, message=exc.message, error_code=exc.code, data={"sync_run_id": run.id})


    def refresh_deadlines(self, views: tuple[str, ...] | None = None, max_pages: int | None = None) -> ToolResult:
        """Refresh just the deadline views -- what read_agenda needs, one login."""
        with self.db.session() as session:
            sync_repo = SyncRunRepository(session)
            run = sync_repo.start()
            try:
                tasks = self.browser.fetch_deadlines(views, max_pages)
                count = TaskRepository(session).upsert_many(tasks)
                sync_repo.finish(run, "success")
                return ToolResult(success=True, message=f"Refreshed {count} task(s)", data={"tasks": count})
            except AppError as exc:
                sync_repo.finish(run, "failed", error_code=exc.code, error_message=exc.message)
                return ToolResult(success=False, message=exc.message, error_code=exc.code)

    def refresh_grades(self, limit: int = 15, now: datetime | None = None) -> ToolResult:
        """Visit a bounded batch of past tasks and record any marks found.

        Marks exist only on a task's own page, so this costs one page load per
        task. The batch is capped to stay inside a caller's timeout; running it
        again picks up where it left off, oldest-unchecked first.
        """
        now = now or datetime.now()
        with self.db.session() as session:
            repo = TaskRepository(session)
            targets = [(t.task_id, t.url, t.title) for t in repo.list_for_grading(max(1, limit), now)]

        if not targets:
            return ToolResult(success=True, message="No past tasks to check", data={"checked": 0, "graded": 0})

        checked = graded = 0
        results: list[dict] = []
        for task_id, url, title in targets:
            try:
                details = self.browser.fetch_task_details(url)
            except AppError as exc:
                return ToolResult(
                    success=False, message=exc.message, error_code=exc.code,
                    data={"checked": checked, "graded": graded},
                )
            checked += 1
            with self.db.session() as session:
                TaskRepository(session).set_assessment(
                    task_id,
                    details.get("grade"),
                    details.get("points_earned"),
                    details.get("points_possible"),
                    details.get("assessment_status"),
                )
            if details.get("grade"):
                graded += 1
                results.append({"task_id": task_id, "title": title, "grade": details.get("grade"),
                                "points": details.get("points")})

        return ToolResult(
            success=True,
            message=f"Checked {checked} task(s), found {graded} mark(s)",
            data={"checked": checked, "graded": graded, "new_grades": results},
        )

    def refresh_timetable(self, start_date: str | None = None, weeks: int = 2) -> ToolResult:
        """Re-scrape the rotation timetable for `weeks` weeks from the given Monday."""
        anchor = date_cls.fromisoformat(start_date) if start_date else date_cls.today()
        monday = anchor - timedelta(days=anchor.weekday())
        starts = [(monday + timedelta(weeks=w)).isoformat() for w in range(max(1, weeks))]

        with self.db.session() as session:
            sync_repo = SyncRunRepository(session)
            run = sync_repo.start()
            try:
                lessons = self.browser.fetch_timetable(starts)
                count = LessonRepository(session).upsert_many(lessons)
                sync_repo.finish(run, "success")
                return ToolResult(
                    success=True,
                    message=f"Refreshed {count} lesson(s) across {len(starts)} week(s)",
                    data={"lessons": count, "weeks": starts},
                )
            except AppError as exc:
                sync_repo.finish(run, "failed", error_code=exc.code, error_message=exc.message)
                return ToolResult(success=False, message=exc.message, error_code=exc.code)


class ReadService:
    def __init__(self, db: Database):
        self.db = db

    def auth_status(self) -> ToolResult:
        return ToolResult(success=True, message="Auth status can be validated by running action_login", data={})

    def classes_last_seen(self) -> "datetime | None":
        with self.db.session() as session:
            return ClassRepository(session).max_last_seen()

    def tasks_last_seen(self, class_id: int) -> "datetime | None":
        with self.db.session() as session:
            return TaskRepository(session).max_last_seen(class_id)

    def tasks_last_seen_any(self) -> "datetime | None":
        with self.db.session() as session:
            return TaskRepository(session).max_last_seen_any()

    def grades(self, subject: str | None = None) -> ToolResult:
        """Marks recorded so far, newest first, with how much of the year is covered."""
        with self.db.session() as session:
            class_map = {c.class_id: c.title for c in ClassRepository(session).list_all()}
            repo = TaskRepository(session)
            rows = []
            for task in repo.list_assessed():
                class_name = class_map.get(task.class_id, str(task.class_id))
                if subject and subject.lower() not in class_name.lower():
                    continue
                rows.append(
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "class_id": task.class_id,
                        "class_name": class_name,
                        "grade": task.grade,
                        "points_earned": task.points_earned,
                        "points_possible": task.points_possible,
                        "due_at": task.due_at.isoformat() if task.due_at else None,
                        "url": task.url,
                    }
                )
            all_tasks = repo.list_all()
            checked = sum(1 for t in all_tasks if t.graded_at is not None)
            total = len(all_tasks)

        by_class: dict[str, list[float]] = {}
        for row in rows:
            try:
                by_class.setdefault(row["class_name"], []).append(float(row["grade"]))
            except (TypeError, ValueError):
                continue
        averages = {name: round(sum(v) / len(v), 2) for name, v in by_class.items() if v}

        return ToolResult(
            success=True,
            message=f"{len(rows)} mark(s) recorded",
            data={
                "grades": rows,
                "average_by_class": averages,
                # Marks are gathered a batch at a time, so say how complete this is
                # rather than let an average look more authoritative than it is.
                "coverage": {"tasks_checked": checked, "tasks_total": total},
            },
        )

    def lessons_last_seen(self) -> datetime | None:
        with self.db.session() as session:
            return LessonRepository(session).max_last_seen()

    def lessons_coverage(self) -> tuple[str | None, str | None]:
        with self.db.session() as session:
            return LessonRepository(session).coverage()

    def schedule(self, date: str | None = None, days: int = 1, now: datetime | None = None) -> ToolResult:
        """Lessons for a date window, each already joined to its class and deadlines."""
        now = now or datetime.now()
        start = date_cls.fromisoformat(date) if date else now.date()
        end = start + timedelta(days=max(1, days) - 1)

        with self.db.session() as session:
            lesson_repo = LessonRepository(session)
            rows = lesson_repo.list_between(start.isoformat(), end.isoformat())
            cov_from, cov_to = lesson_repo.coverage()
            # Keyed by class AND day: a deadline belongs to the lesson sitting
            # on its own date, not to every lesson of that class in the window.
            tasks_by_slot: dict[tuple[int, str], list[dict]] = {}
            for task in TaskRepository(session).list_all():
                if task.due_at is None:
                    continue
                due_date = task.due_at.date()
                if start <= due_date <= end:
                    tasks_by_slot.setdefault((task.class_id, due_date.isoformat()), []).append(
                        {"task_id": task.task_id, "title": task.title, "due_at": task.due_at.isoformat()}
                    )

            days_out: dict[str, list[dict]] = {}
            for row in rows:
                days_out.setdefault(row.date, []).append(
                    {
                        "period": row.period,
                        "title": row.title,
                        "class_id": row.class_id,
                        "teacher": row.teacher,
                        "room": row.room,
                        "starts_at": row.starts_at,
                        "ends_at": row.ends_at,
                        "rotation_day": row.rotation_day,
                        # Deadlines landing on this lesson's own day, so "what is
                        # due in maths tomorrow" needs no second call.
                        "due_today": tasks_by_slot.get((row.class_id, row.date), []) if row.class_id else [],
                    }
                )

        schedule = [{"date": d, "lessons": days_out[d]} for d in sorted(days_out)]
        return ToolResult(
            success=True,
            message=f"{sum(len(d['lessons']) for d in schedule)} lesson(s) over {len(schedule)} day(s)",
            data={
                "from": start.isoformat(),
                "to": end.isoformat(),
                "days": schedule,
                # So an empty result can be told apart from a window the cache
                # never covered.
                "cached_range": {"from": cov_from, "to": cov_to},
            },
        )

    def agenda(
        self,
        view: str = "upcoming",
        within_days: int | None = None,
        subject: str | None = None,
        now: datetime | None = None,
    ) -> ToolResult:
        """Cross-class deadline list, sorted by due date, with class names and human due phrasing.

        view: upcoming (due in the future), overdue (past due), today, week (next 7 days),
        all (everything). When within_days is given it overrides view with a forward window.
        subject is a case-insensitive substring match on the class name.
        """
        now = now or datetime.utcnow()
        rows: list[dict] = []
        with self.db.session() as session:
            class_map = {c.class_id: c.title for c in ClassRepository(session).list_all()}
            for t in TaskRepository(session).list_all():
                class_name = class_map.get(t.class_id, str(t.class_id))
                if subject and subject.lower() not in class_name.lower():
                    continue
                due = t.due_at

                if within_days is not None:
                    if due is None or due < now or due > now + timedelta(days=within_days):
                        continue
                elif view == "overdue":
                    if due is None or due >= now:
                        continue
                elif view == "today":
                    if due is None or due.date() != now.date():
                        continue
                elif view == "week":
                    if due is None or due < now or due > now + timedelta(days=7):
                        continue
                elif view == "upcoming":
                    if due is None or due < now:
                        continue
                # view == "all" -> no filter

                rows.append(
                    {
                        "task_id": t.task_id,
                        "title": t.title,
                        "class_id": t.class_id,
                        "class_name": class_name,
                        "status": t.status,
                        "due_at": due.isoformat() if due else None,
                        "due_relative": _relative_due(due, now),
                        "url": t.url,
                        "dropbox_url": t.dropbox_url,
                    }
                )

        rows.sort(key=lambda r: (r["due_at"] is None, r["due_at"] or ""))
        return ToolResult(
            success=True,
            message=f"Returned {len(rows)} task(s) for view '{within_days and f'next {within_days}d' or view}'",
            data={"view": view, "within_days": within_days, "subject": subject, "tasks": rows},
        )

    def cas_last_seen(self) -> "datetime | None":
        with self.db.session() as session:
            return CasRepository(session).max_last_seen()

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
            # The crawl walks every view for every class anyway, so storing only
            # the requested one threw away work already paid for -- and left the
            # other classes on stale data.
            tasks = self.browser.fetch_all_tasks()
            count = TaskRepository(session).upsert_many(tasks)
            mine = sum(1 for t in tasks if t.class_id == class_id)
            return ToolResult(
                success=True,
                message=f"Refreshed {mine} task(s) for this class, {count} in total",
                data={"tasks": mine, "tasks_total": count, "class_id": class_id},
            )

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

    def submit_task_content(self, task_id: int, file_name: str, content_base64: str, comment: str | None = None) -> ToolResult:
        """Submit a dropbox file from inline base64 content.

        The connector runs on a server with no access to the caller's filesystem,
        so remote agents send the bytes inline instead of a path. The content is
        written to a private temp file, uploaded, then removed.
        """
        import base64
        import os
        import tempfile

        try:
            raw = base64.b64decode(content_base64, validate=True)
        except Exception:
            return ToolResult(success=False, message="content_base64 is not valid base64", error_code=INVALID_INPUT)
        if not raw:
            return ToolResult(success=False, message="content_base64 decoded to empty bytes", error_code=INVALID_INPUT)

        safe_name = os.path.basename(file_name or "").strip() or "submission"
        tmp_dir = Path(tempfile.mkdtemp(prefix="mb_submit_"))
        tmp_path = tmp_dir / safe_name
        try:
            tmp_path.write_bytes(raw)
            return self.submit_task_file(task_id=task_id, file_path=str(tmp_path), comment=comment)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    def retry_submission(self, task_id: int, file_path: str) -> ToolResult:
        return self.submit_task_file(task_id=task_id, file_path=file_path)

    def task_details_live(self, task_id: int) -> ToolResult:
        """Open the task's own page and read what the student actually has to do."""
        with self.db.session() as session:
            task = TaskRepository(session).get(task_id)
            if task is None:
                return ToolResult(success=False, message=f"Task {task_id} not found", error_code=TASK_NOT_FOUND)
            url, title = task.url, task.title
        details = self.browser.fetch_task_details(url)
        # Reading the page is the only way to see a mark, so never waste the visit.
        with self.db.session() as session:
            TaskRepository(session).set_assessment(
                task_id,
                details.get("grade"),
                details.get("points_earned"),
                details.get("points_possible"),
                details.get("assessment_status"),
            )
        return ToolResult(
            success=True,
            message=f"Task details for '{title}'",
            data={"task_id": task_id, "title": title, **details},
        )

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
