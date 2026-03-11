"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class ClassEntity(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher: Mapped[Optional[str]] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class TaskEntity(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    class_id: Mapped[int] = mapped_column(Integer, ForeignKey("classes.class_id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[Optional[str]] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    dropbox_url: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class TaskSubmission(Base):
    __tablename__ = "task_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.task_id"), nullable=False, index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    result_status: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    artifact_path: Mapped[Optional[str]] = mapped_column(Text)


class CasExperience(Base):
    __tablename__ = "cas_experiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experience_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(64))
    start_date: Mapped[Optional[str]] = mapped_column(String(32))
    end_date: Mapped[Optional[str]] = mapped_column(String(32))
    hours: Mapped[Optional[float]] = mapped_column(Float)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CasReflection(Base):
    __tablename__ = "cas_reflections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reflection_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    experience_id: Mapped[int] = mapped_column(Integer, ForeignKey("cas_experiences.experience_id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_preview: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("reflection_id", "experience_id", name="uq_reflection_experience"),
    )


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    html_path: Mapped[Optional[str]] = mapped_column(Text)
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SelectorVersion(Base):
    __tablename__ = "selector_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    selector_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
