"""MCP server wiring for ManageBac tools."""

from __future__ import annotations

from datetime import date as _date, datetime, timedelta
from typing import Any

from .config import Settings, load_managebac_config
from .credentials import parse_basic_auth, require_credentials, set_resolver
from .db import Database
from .services import ActionService, ReadService, SyncService
from .types import ToolResult


def _serialize(result: ToolResult) -> dict[str, Any]:
    return result.to_dict()


def _is_stale(last_seen, max_age_minutes: int | None) -> bool:
    """Read-through helper: is cached data older than max_age_minutes?

    max_age_minutes is None -> never refresh (pure cache read). No cached data
    at all -> always refresh. last_seen is stored in UTC (datetime.utcnow).
    """
    if max_age_minutes is None:
        return False
    if last_seen is None:
        return True
    return datetime.utcnow() - last_seen > timedelta(minutes=max_age_minutes)


def create_services() -> tuple[Settings, Database, SyncService, ReadService, ActionService]:
    from .browser import PlaywrightBrowserGateway

    settings = Settings()
    cfg = load_managebac_config(settings.managebac_config_path)
    db = Database(settings.sqlalchemy_url)
    db.create_all()

    browser = PlaywrightBrowserGateway(cfg)
    sync_service = SyncService(db, browser)
    read_service = ReadService(db)
    action_service = ActionService(db, browser)
    return settings, db, sync_service, read_service, action_service


def create_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:
        raise RuntimeError("`mcp` package is required. Install with `pip install .[server]`") from exc

    settings, _, sync_service, read_service, action_service = create_services()
    cfg = load_managebac_config(settings.managebac_config_path)

    mcp = FastMCP("managebac-student-mcp")
    from mcp.types import ToolAnnotations
    _RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
    _WR = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

    def _request_credentials():
        """Read the current request's Basic-auth ManageBac credentials.

        Accessing the request context outside of a live request raises, so the
        whole lookup is guarded: no request (e.g. startup sync) yields no
        credentials rather than an error.
        """
        try:
            request = mcp.get_context().request_context.request
            if request is None:
                return None
            return parse_basic_auth(request.headers.get("authorization"))
        except Exception:
            return None

    set_resolver(_request_credentials)

    if cfg.features.startup_sync:
        sync_service.run_startup_sync()

    @mcp.tool(name="whoami", annotations=_RO)
    def whoami() -> dict[str, Any]:
        """Verify the supplied credentials by logging in; used as the connection identity."""
        username, password = require_credentials(cfg)
        action_service.login(username, password)
        return {"id": username, "name": username, "verified": True}

    @mcp.tool(name="read_auth_status", annotations=_RO)
    def read_auth_status() -> dict[str, Any]:
        """Report whether the connection is usable. Cheap; does not log in."""
        return _serialize(read_service.auth_status())

    @mcp.tool(name="action_login", annotations=_RO)
    def action_login() -> dict[str, Any]:
        """Force a ManageBac login to check the stored credentials. Rarely needed --
        every other tool logs in on its own when it has to."""
        username, password = require_credentials(cfg)
        return _serialize(action_service.login(username, password))

    @mcp.tool(name="action_startup_sync", annotations=_WR)
    def action_startup_sync() -> dict[str, Any]:
        """Re-scrape everything (classes, all tasks, CAS, this week's and next week's
        lessons) in one login and refill the cache.

        Advanced/maintenance: the read_* tools refresh themselves via max_age_minutes,
        so prefer read_agenda. Use this only to force a full rebuild of the cache.

        Slow -- it walks every page of every deadline view and can run past a
        client's timeout while still completing on the server. Do not retry it on
        timeout; read_agenda afterwards to see the result.
        """
        return _serialize(sync_service.run_startup_sync())

    @mcp.tool(name="read_classes", annotations=_RO)
    def read_classes(max_age_minutes: int | None = None) -> dict[str, Any]:
        """List the student's classes (id, title, teacher, url).

        Auto-refreshes when the cache is older than max_age_minutes (default: cache only).
        To answer "what is due?", prefer read_agenda -- it already names the class.
        """
        if _is_stale(read_service.classes_last_seen(), max_age_minutes):
            action_service.refresh_classes()
        return _serialize(read_service.list_classes())

    @mcp.tool(name="read_agenda", annotations=_RO)
    def read_agenda(
        view: str = "upcoming",
        within_days: int | None = None,
        subject: str | None = None,
        max_age_minutes: int | None = 15,
    ) -> dict[str, Any]:
        """Deadlines across ALL classes in one call — the fastest way to answer "what's due?".

        Returns tasks sorted by due date, each with the class NAME (not just id), the due
        date in ISO plus human phrasing ("in 3 days" / "overdue by 2 hours"), and status —
        so no per-class or id lookups are needed.

        view: "upcoming" (default, future deadlines), "overdue", "today", "week" (next 7 days),
        or "all". within_days=N overrides view with a rolling N-day forward window. subject is a
        case-insensitive class-name filter (e.g. "math"). Data is auto-refreshed when older than
        max_age_minutes (default 15; pass None to read cache only, or 0 to force a refresh).
        """
        if _is_stale(read_service.tasks_last_seen_any(), max_age_minutes):
            sync_service.refresh_deadlines()
        return _serialize(read_service.agenda(view=view, within_days=within_days, subject=subject))

    @mcp.tool(name="read_grades", annotations=_RO)
    def read_grades(subject: str | None = None) -> dict[str, Any]:
        """Marks recorded so far, newest first, plus an average per class.

        This school's ManageBac shows no gradebook to students: a mark exists only
        on the task's own page. So grades are collected a batch at a time by
        action_refresh_grades and served from cache here. The coverage field says
        how many tasks have been checked out of the total -- read it before
        treating an average as the student's real standing.

        subject is a case-insensitive class-name filter (e.g. "math").
        """
        return _serialize(read_service.grades(subject=subject))

    @mcp.tool(name="action_refresh_grades", annotations=_WR)
    def action_refresh_grades(limit: int = 15) -> dict[str, Any]:
        """Check a batch of past tasks for marks and record what is found.

        Costs one page load per task, so limit is capped to stay inside the
        caller's timeout; call it again to continue, oldest-unchecked first.
        Read the results with read_grades.
        """
        return _serialize(sync_service.refresh_grades(limit=min(max(1, limit), 25)))

    @mcp.tool(name="read_schedule", annotations=_RO)
    def read_schedule(
        date: str | None = None,
        days: int = 1,
        max_age_minutes: int | None = 720,
    ) -> dict[str, Any]:
        """Lesson timetable — answers "what do I have today/tomorrow?" and when.

        Returns each day's lessons in order with period, start/end time, subject,
        teacher, room, and the rotation day. Every lesson also carries due_today:
        the deadlines for that same class falling on that date, so planning a day
        takes one call.

        date: ISO "2026-09-07" (default: today). days: how many days to return
        (default 1; use 7 for the week ahead). The school runs a ten-day rotation,
        so each date genuinely differs -- do not assume next week repeats this one.
        Auto-refreshes when the cache is older than max_age_minutes (default 720).
        """
        # A fresh cache still misses a week we never fetched, so coverage is
        # checked as well as age -- otherwise a future week returns a silent [].
        window_start = _date.fromisoformat(date) if date else _date.today()
        window_end = window_start + timedelta(days=max(1, days) - 1)
        cov_from, cov_to = read_service.lessons_coverage()
        uncovered = (
            cov_from is None
            or window_start.isoformat() < cov_from
            or window_end.isoformat() > cov_to
        )
        if uncovered or _is_stale(read_service.lessons_last_seen(), max_age_minutes):
            weeks = max(1, (window_end - window_start).days // 7 + 2)
            sync_service.refresh_timetable(start_date=window_start.isoformat(), weeks=weeks)
        return _serialize(read_service.schedule(date=date, days=days))

    @mcp.tool(name="action_refresh_timetable", annotations=_WR)
    def action_refresh_timetable(start_date: str | None = None, weeks: int = 2) -> dict[str, Any]:
        """Force a re-scrape of the timetable. Advanced: read_schedule(max_age_minutes=0)
        does the same thing and returns the data."""
        return _serialize(sync_service.refresh_timetable(start_date=start_date, weeks=weeks))

    @mcp.tool(name="action_refresh_classes", annotations=_WR)
    def action_refresh_classes() -> dict[str, Any]:
        """Force a re-scrape of the class list. Advanced: read_classes(max_age_minutes=0)
        does the same thing and returns the data."""
        sync_service.refresh_grades(limit=8)  # TEMP verification hook
        return _serialize(action_service.refresh_classes())

    @mcp.tool(name="read_class_details", annotations=_RO)
    def read_class_details(class_id: int) -> dict[str, Any]:
        """Return one class's title, teacher and url, by class id (see read_classes)."""
        return _serialize(read_service.class_details(class_id))

    @mcp.tool(name="read_class_tasks", annotations=_RO)
    def read_class_tasks(class_id: int, max_age_minutes: int | None = None) -> dict[str, Any]:
        """Tasks for ONE class, sorted by due date, from the cache.

        Auto-refreshes when older than max_age_minutes (default: cache only). For
        "what's due" across all classes, or filtering by subject name instead of a
        class id, use read_agenda instead.
        """
        if _is_stale(read_service.tasks_last_seen(class_id), max_age_minutes):
            action_service.refresh_class_tasks(class_id)
        return _serialize(read_service.class_tasks(class_id))

    @mcp.tool(name="action_refresh_class_tasks", annotations=_WR)
    def action_refresh_class_tasks(class_id: int) -> dict[str, Any]:
        """Force a re-scrape of one class's tasks. Advanced: prefer
        read_class_tasks(class_id, max_age_minutes=0), which also returns the data."""
        return _serialize(action_service.refresh_class_tasks(class_id))

    @mcp.tool(name="read_task", annotations=_RO)
    def read_task(task_id: int) -> dict[str, Any]:
        """Cached facts about one task: title, class, due date, status, urls.

        For the teacher's actual instructions and submission state, use read_task_details.
        """
        return _serialize(read_service.task_details(task_id))

    @mcp.tool(name="read_task_details", annotations=_RO)
    def read_task_details(task_id: int) -> dict[str, Any]:
        """Open one task's page and return what the student actually has to do.

        Unlike read_task (which serves the cached title/due/status), this fetches the
        task page live and returns the teacher's description/instructions, any files
        the teacher attached (name, size and a direct download url), the task labels
        (e.g. "Summative", "Essay"), the status, the due text, and whether anything
        has been submitted yet. Costs a login, so use it for a single task
        the student is actually working on -- use read_agenda to decide which.
        """
        return _serialize(action_service.task_details_live(task_id))

    @mcp.tool(name="read_task_dropbox", annotations=_RO)
    def read_task_dropbox(task_id: int) -> dict[str, Any]:
        """Return the dropbox (submission) URL for a task. The submit tools use it
        automatically, so you rarely need this directly."""
        return _serialize(read_service.task_dropbox(task_id))

    @mcp.tool(name="action_submit_task_file", annotations=_WR)
    def action_submit_task_file(task_id: int, file_path: str, comment: str | None = None) -> dict[str, Any]:
        """Submit a local file to a task's dropbox by server-side path (CLI/local use)."""
        return _serialize(action_service.submit_task_file(task_id=task_id, file_path=file_path, comment=comment))

    @mcp.tool(name="action_submit_task_content", annotations=_WR)
    def action_submit_task_content(task_id: int, file_name: str, content_base64: str, comment: str | None = None) -> dict[str, Any]:
        """Submit a file to a task's dropbox from inline base64 content.

        Use this from a remote agent that cannot place a file on the server: pass the
        file's bytes as base64 in content_base64 and the desired file_name (e.g.
        "essay.pdf"). The server writes it to a temp file, uploads it to ManageBac, and
        deletes it. Prefer this over action_submit_task_file when you only have the content.
        """
        return _serialize(
            action_service.submit_task_content(task_id=task_id, file_name=file_name, content_base64=content_base64, comment=comment)
        )

    @mcp.tool(name="read_submission_result", annotations=_RO)
    def read_submission_result(task_id: int) -> dict[str, Any]:
        """Return the most recent submission this connector made for a task
        (file name, time, resulting status). Use it to confirm an upload landed."""
        return _serialize(read_service.submission_result(task_id))

    @mcp.tool(name="action_retry_submission", annotations=_WR)
    def action_retry_submission(task_id: int, file_path: str) -> dict[str, Any]:
        """Re-upload a file to a task's dropbox after a failed attempt (server-side path)."""
        return _serialize(action_service.retry_submission(task_id=task_id, file_path=file_path))

    @mcp.tool(name="read_cas_dashboard", annotations=_RO)
    def read_cas_dashboard(max_age_minutes: int | None = None) -> dict[str, Any]:
        """All CAS experiences with approval status, hours, and start/end dates.

        Answers "how many CAS hours do I have?" -- sum the hours field. Auto-refreshes
        when the cache is older than max_age_minutes (default: cache only).
        """
        if _is_stale(read_service.cas_last_seen(), max_age_minutes):
            action_service.refresh_cas()
        return _serialize(read_service.cas_dashboard())

    @mcp.tool(name="action_refresh_cas", annotations=_WR)
    def action_refresh_cas() -> dict[str, Any]:
        """Force a re-scrape of CAS. Advanced: prefer
        read_cas_dashboard(max_age_minutes=0), which also returns the data."""
        return _serialize(action_service.refresh_cas())

    @mcp.tool(name="read_cas_experience", annotations=_RO)
    def read_cas_experience(experience_id: int) -> dict[str, Any]:
        """One CAS experience by id: title, status, hours, dates (see read_cas_dashboard)."""
        return _serialize(read_service.cas_experience(experience_id))

    @mcp.tool(name="action_create_cas_experience", annotations=_WR)
    def action_create_cas_experience(payload: dict[str, Any]) -> dict[str, Any]:
        """Create a CAS experience. payload takes "name" and "description"; it only fills
        the form unless you also pass "submit": true."""
        return _serialize(action_service.create_cas_experience(payload))

    @mcp.tool(name="read_cas_reflections", annotations=_RO)
    def read_cas_reflections(experience_id: int) -> dict[str, Any]:
        """Reflections this connector has added to a CAS experience."""
        return _serialize(read_service.cas_reflections(experience_id))

    @mcp.tool(name="action_add_reflection_journal", annotations=_WR)
    def action_add_reflection_journal(experience_id: int, text: str, outcomes: list[str]) -> dict[str, Any]:
        """Add a written journal reflection to a CAS experience.

        outcomes are IB learning outcome names, e.g. ["Awareness", "Collaboration"].
        """
        return _serialize(action_service.add_reflection_journal(experience_id=experience_id, text=text, outcomes=outcomes))

    @mcp.tool(name="action_add_reflection_file", annotations=_WR)
    def action_add_reflection_file(experience_id: int, file_path: str, outcomes: list[str]) -> dict[str, Any]:
        """Attach a file as evidence to a CAS experience (server-side path)."""
        return _serialize(action_service.add_reflection_file(experience_id=experience_id, file_path=file_path, outcomes=outcomes))

    @mcp.tool(name="action_add_reflection_video", annotations=_WR)
    def action_add_reflection_video(experience_id: int, video_url: str, outcomes: list[str]) -> dict[str, Any]:
        """Attach a video URL as evidence to a CAS experience."""
        return _serialize(
            action_service.add_reflection_link(experience_id=experience_id, reflection_type="video", url=video_url, outcomes=outcomes)
        )

    @mcp.tool(name="action_add_reflection_website", annotations=_WR)
    def action_add_reflection_website(experience_id: int, website_url: str, outcomes: list[str]) -> dict[str, Any]:
        """Attach a website URL as evidence to a CAS experience."""
        return _serialize(
            action_service.add_reflection_link(experience_id=experience_id, reflection_type="website", url=website_url, outcomes=outcomes)
        )

    @mcp.tool(name="action_add_reflection_photos", annotations=_WR)
    def action_add_reflection_photos(experience_id: int, photos_url: str, outcomes: list[str]) -> dict[str, Any]:
        """Attach a photo-album URL as evidence to a CAS experience."""
        return _serialize(
            action_service.add_reflection_link(experience_id=experience_id, reflection_type="photos", url=photos_url, outcomes=outcomes)
        )

    return mcp
