"""Data access layer."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Iterable

from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.orm import Session

from .schema import (
    CasExperience,
    CasReflection,
    ClassEntity,
    LessonEntity,
    PageSnapshot,
    SyncRun,
    TaskEntity,
    TaskSubmission,
)
from .types import CasExperienceRecord, ClassRecord, LessonRecord, TaskRecord


def _now() -> datetime:
    return datetime.utcnow()


class SyncRunRepository:
    def __init__(self, session: Session):
        self.session = session

    def start(self) -> SyncRun:
        row = SyncRun(started_at=_now(), status="running")
        self.session.add(row)
        self.session.flush()
        return row

    def finish(self, sync_run: SyncRun, status: str, error_code: str | None = None, error_message: str | None = None) -> None:
        sync_run.finished_at = _now()
        sync_run.status = status
        sync_run.error_code = error_code
        sync_run.error_message = error_message


class ClassRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_many(self, records: Iterable[ClassRecord]) -> int:
        count = 0
        for record in records:
            row = self.session.execute(select(ClassEntity).where(ClassEntity.class_id == record.class_id)).scalar_one_or_none()
            if row is None:
                row = ClassEntity(class_id=record.class_id, title=record.title, teacher=record.teacher, url=record.url, raw_hash=record.raw_hash, last_seen_at=_now())
                self.session.add(row)
            else:
                row.title = record.title
                row.teacher = record.teacher
                row.url = record.url
                row.raw_hash = record.raw_hash
                row.last_seen_at = _now()
            count += 1
        return count

    def list_all(self) -> list[ClassEntity]:
        return list(self.session.execute(select(ClassEntity).order_by(ClassEntity.title.asc())).scalars().all())

    def get(self, class_id: int) -> ClassEntity | None:
        return self.session.execute(select(ClassEntity).where(ClassEntity.class_id == class_id)).scalar_one_or_none()

    def max_last_seen(self) -> datetime | None:
        return self.session.execute(select(func.max(ClassEntity.last_seen_at))).scalar()


class TaskRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_many(self, records: Iterable[TaskRecord]) -> int:
        count = 0
        for record in records:
            row = self.session.execute(select(TaskEntity).where(TaskEntity.task_id == record.task_id)).scalar_one_or_none()
            if row is None:
                row = TaskEntity(
                    task_id=record.task_id,
                    class_id=record.class_id,
                    title=record.title,
                    due_at=record.due_at,
                    status=record.status,
                    url=record.url,
                    dropbox_url=record.dropbox_url,
                    raw_hash=record.raw_hash,
                    last_seen_at=_now(),
                )
                self.session.add(row)
            else:
                row.class_id = record.class_id
                row.title = record.title
                row.due_at = record.due_at
                row.status = record.status
                row.url = record.url
                row.dropbox_url = record.dropbox_url
                row.raw_hash = record.raw_hash
                row.last_seen_at = _now()
            count += 1
        return count

    def list_by_class(self, class_id: int) -> list[TaskEntity]:
        return list(
            self.session.execute(
                select(TaskEntity).where(TaskEntity.class_id == class_id).order_by(TaskEntity.due_at.asc().nulls_last(), TaskEntity.title.asc())
            ).scalars().all()
        )

    def list_all(self) -> list[TaskEntity]:
        return list(
            self.session.execute(
                select(TaskEntity).order_by(TaskEntity.due_at.asc().nulls_last(), TaskEntity.title.asc())
            ).scalars().all()
        )

    def get(self, task_id: int) -> TaskEntity | None:
        return self.session.execute(select(TaskEntity).where(TaskEntity.task_id == task_id)).scalar_one_or_none()

    def max_last_seen(self, class_id: int) -> datetime | None:
        return self.session.execute(
            select(func.max(TaskEntity.last_seen_at)).where(TaskEntity.class_id == class_id)
        ).scalar()

    def set_assessment(
        self,
        task_id: int,
        grade: str | None,
        points_earned: float | None,
        points_possible: float | None,
        assessment_status: str | None,
    ) -> bool:
        row = self.get(task_id)
        if row is None:
            return False
        row.grade = grade
        row.points_earned = points_earned
        row.points_possible = points_possible
        row.assessment_status = assessment_status
        # Stamped even when nothing was awarded, so the next sweep moves on to
        # tasks that have never been looked at.
        row.graded_at = _now()
        return True

    def list_assessed(self) -> list[TaskEntity]:
        return list(
            self.session.execute(
                select(TaskEntity)
                .where(TaskEntity.grade.is_not(None))
                .order_by(TaskEntity.due_at.desc().nulls_last())
            ).scalars().all()
        )

    def list_for_grading(self, limit: int, before: datetime) -> list[TaskEntity]:
        """Past tasks to check for a mark, most promising first.

        Only submitted work can carry a mark, and the deadline tiles already tell
        us which tasks those are -- so they go first, ahead of the far larger pile
        of tasks that were never handed in. Within each group, never-checked
        before rechecked, then most recent.
        """
        submitted_first = case((TaskEntity.status == "Submitted", 0), else_=1)
        return list(
            self.session.execute(
                select(TaskEntity)
                # Handed-in work qualifies whatever its deadline: a third of it
                # here was submitted early and would never be reached otherwise.
                .where(
                    or_(
                        TaskEntity.status == "Submitted",
                        and_(TaskEntity.due_at.is_not(None), TaskEntity.due_at <= before),
                    )
                )
                .order_by(
                    submitted_first,
                    TaskEntity.graded_at.asc().nulls_first(),
                    TaskEntity.due_at.desc(),
                )
                .limit(limit)
            ).scalars().all()
        )

    def max_last_seen_any(self) -> datetime | None:
        return self.session.execute(select(func.max(TaskEntity.last_seen_at))).scalar()


class SubmissionRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, task_id: int, file_name: str, result_status: str, message: str | None, artifact_path: str | None) -> TaskSubmission:
        row = TaskSubmission(
            task_id=task_id,
            submitted_at=_now(),
            file_name=file_name,
            result_status=result_status,
            message=message,
            artifact_path=artifact_path,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def last_for_task(self, task_id: int) -> TaskSubmission | None:
        return (
            self.session.execute(select(TaskSubmission).where(TaskSubmission.task_id == task_id).order_by(desc(TaskSubmission.submitted_at))).scalars().first()
        )


class CasRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_experiences(self, records: Iterable[CasExperienceRecord]) -> int:
        count = 0
        for record in records:
            row = self.session.execute(select(CasExperience).where(CasExperience.experience_id == record.experience_id)).scalar_one_or_none()
            if row is None:
                row = CasExperience(
                    experience_id=record.experience_id,
                    title=record.title,
                    status=record.status,
                    start_date=record.start_date,
                    end_date=record.end_date,
                    hours=record.hours,
                    url=record.url,
                    raw_hash=record.raw_hash,
                    last_seen_at=_now(),
                )
                self.session.add(row)
            else:
                row.title = record.title
                row.status = record.status
                row.start_date = record.start_date
                row.end_date = record.end_date
                row.hours = record.hours
                row.url = record.url
                row.raw_hash = record.raw_hash
                row.last_seen_at = _now()
            count += 1
        return count

    def list_experiences(self) -> list[CasExperience]:
        return list(self.session.execute(select(CasExperience).order_by(CasExperience.title.asc())).scalars().all())

    def max_last_seen(self) -> datetime | None:
        return self.session.execute(select(func.max(CasExperience.last_seen_at))).scalar()

    def get_experience(self, experience_id: int) -> CasExperience | None:
        return self.session.execute(select(CasExperience).where(CasExperience.experience_id == experience_id)).scalar_one_or_none()

    def create_reflection(self, experience_id: int, reflection_type: str, content_preview: str | None, url: str | None, reflection_id: int | None = None) -> CasReflection:
        row = CasReflection(
            reflection_id=reflection_id,
            experience_id=experience_id,
            type=reflection_type,
            content_preview=content_preview,
            url=url,
            created_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_reflections(self, experience_id: int) -> list[CasReflection]:
        return list(
            self.session.execute(
                select(CasReflection).where(CasReflection.experience_id == experience_id).order_by(desc(CasReflection.created_at))
            ).scalars().all()
        )


class SnapshotRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, page_type: str, entity_id: str | None, html_path: str | None, screenshot_path: str | None) -> PageSnapshot:
        row = PageSnapshot(
            page_type=page_type,
            entity_id=entity_id,
            html_path=html_path,
            screenshot_path=screenshot_path,
            captured_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row


class LessonRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_many(self, records: Iterable[LessonRecord]) -> int:
        count = 0
        for record in records:
            row = self.session.execute(
                select(LessonEntity).where(
                    LessonEntity.date == record.date,
                    LessonEntity.period == record.period,
                    LessonEntity.title == record.title,
                )
            ).scalar_one_or_none()
            if row is None:
                row = LessonEntity(
                    date=record.date,
                    period=record.period,
                    class_id=record.class_id,
                    title=record.title,
                    grade=record.grade,
                    teacher=record.teacher,
                    room=record.room,
                    starts_at=record.starts_at,
                    ends_at=record.ends_at,
                    rotation_day=record.rotation_day,
                    raw_hash=record.raw_hash,
                    last_seen_at=_now(),
                )
                self.session.add(row)
            else:
                row.class_id = record.class_id
                row.grade = record.grade
                row.teacher = record.teacher
                row.room = record.room
                row.starts_at = record.starts_at
                row.ends_at = record.ends_at
                row.rotation_day = record.rotation_day
                row.raw_hash = record.raw_hash
                row.last_seen_at = _now()
            count += 1
        return count

    def list_between(self, start_date: str, end_date: str) -> list[LessonEntity]:
        return list(
            self.session.execute(
                select(LessonEntity)
                .where(LessonEntity.date >= start_date, LessonEntity.date <= end_date)
                .order_by(LessonEntity.date.asc(), LessonEntity.starts_at.asc().nulls_first())
            ).scalars().all()
        )

    def max_last_seen(self) -> datetime | None:
        return self.session.execute(select(func.max(LessonEntity.last_seen_at))).scalar()

    def coverage(self) -> tuple[str | None, str | None]:
        """Earliest and latest dates the cache holds, to spot uncovered windows."""
        row = self.session.execute(
            select(func.min(LessonEntity.date), func.max(LessonEntity.date))
        ).one()
        return row[0], row[1]
