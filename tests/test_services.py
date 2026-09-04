from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from managebac_mcp.browser import UploadOutcome
from managebac_mcp.db import Database
from managebac_mcp.services import ActionService, ReadService, SyncService
from managebac_mcp.types import CasExperienceRecord, ClassRecord, LessonRecord, TaskRecord


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

    def fetch_all_tasks(self):
        out = []
        for cls in self.fetch_classes():
            out.extend(self.fetch_tasks(cls.class_id))
        return out

    def fetch_deadlines(self, views=None, max_pages=None):
        out = []
        for cls in self.fetch_classes():
            out.extend(self.fetch_tasks(cls.class_id))
        return out

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

    def fetch_task_details(self, task_url: str) -> dict:
        return {
            "description": "Write the essay.",
            "attachments": [{"name": "brief.pdf", "url": "https://x/attachments/abc", "size": "1.03 MB"}],
            "labels": ["Summative", "Essay"],
            "status": "Pending",
            "due_text": "Tuesday at 8:25 AM",
            "submission_status": "Not Submitted",
            "url": task_url,
        }

    def fetch_timetable(self, start_dates: list[str]) -> list[LessonRecord]:
        return [
            LessonRecord(
                date="2026-09-07", period="1", class_id=12816550, title="Russian A SL",
                grade="DP 2", teacher="Tatiana Komova", room="D1",
                starts_at="08:40", ends_at="09:40", rotation_day="6", raw_hash="l1",
            )
        ]

    def collect_startup_data(self):
        classes = self.fetch_classes()
        tasks_by_class = {cls.class_id: self.fetch_tasks(cls.class_id) for cls in classes}
        return classes, tasks_by_class, self.fetch_cas_experiences(), self.fetch_timetable([])

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


def test_submit_task_content_base64(tmp_path: Path):
    import base64

    db = build_db()
    browser = FakeBrowser()
    SyncService(db, browser).run_startup_sync()

    action = ActionService(db, browser)
    read = ReadService(db)

    task_id = 47417931 + 12816550
    content = base64.b64encode(b"hello world").decode("ascii")
    submit = action.submit_task_content(task_id=task_id, file_name="essay.pdf", content_base64=content)
    assert submit.success

    result = read.submission_result(task_id)
    assert result.success
    assert result.data["submission"]["file_name"] == "essay.pdf"

    bad = action.submit_task_content(task_id=task_id, file_name="x", content_base64="!!!not base64!!!")
    assert not bad.success
    assert bad.error_code == "INVALID_INPUT"


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


def test_parse_due_infers_closest_year_and_handles_garbage():
    from managebac_mcp.browser import PlaywrightBrowserGateway as G

    d = G._parse_due("Jun 23, 8:25 AM")
    assert d is not None and (d.month, d.day, d.hour, d.minute) == (6, 23, 8, 25)
    assert G._parse_due("") is None
    assert G._parse_due("not a date") is None


def test_parse_cas_hours_and_dates():
    from managebac_mcp.browser import PlaywrightBrowserGateway as G

    assert G._parse_hours("20 hours") == 20.0
    assert G._parse_hours("1.5 hours") == 1.5
    assert G._parse_hours("") is None
    assert G._parse_hours("no number here") is None

    assert G._parse_cas_dates("Mar 09, 2026 \n - \n Mar 21, 2026") == ("2026-03-09", "2026-03-21")
    assert G._parse_cas_dates("Sep 22, 2025") == ("2025-09-22", None)
    assert G._parse_cas_dates("") == (None, None)


def test_task_details_live():
    db = build_db()
    browser = FakeBrowser()
    SyncService(db, browser).run_startup_sync()
    action = ActionService(db, browser)

    res = action.task_details_live(47417931 + 12816550)
    assert res.success
    assert res.data["description"] == "Write the essay."
    assert res.data["labels"] == ["Summative", "Essay"]
    assert res.data["attachments"][0]["name"] == "brief.pdf"
    assert res.data["submission_status"] == "Not Submitted"

    missing = action.task_details_live(999999)
    assert not missing.success
    assert missing.error_code == "TASK_NOT_FOUND"


def test_relative_due_phrasing():
    from managebac_mcp.services import _relative_due

    now = datetime(2026, 9, 3, 12, 0, 0)
    assert _relative_due(None, now) is None
    assert _relative_due(datetime(2026, 9, 6, 12, 0, 0), now) == "in 3 days"
    assert _relative_due(datetime(2026, 9, 1, 12, 0, 0), now) == "overdue by 2 days"
    assert _relative_due(datetime(2026, 9, 3, 15, 0, 0), now) == "in 3 hours"
    assert _relative_due(datetime(2026, 9, 3, 11, 30, 0), now) == "overdue by 30 minutes"


def test_agenda_views_and_subject_filter():
    db = build_db()
    browser = FakeBrowser()
    read = ReadService(db)

    # Seed two classes with tasks at different due dates directly through the sync path,
    # then override due dates so we can exercise view filters deterministically.
    from managebac_mcp.repositories import ClassRepository, TaskRepository
    from managebac_mcp.types import ClassRecord, TaskRecord

    now = datetime(2026, 9, 10, 12, 0, 0)
    with db.session() as session:
        ClassRepository(session).upsert_many(
            [
                ClassRecord(class_id=100, title="Mathematics HL", teacher="A", url="https://x/classes/100", raw_hash="h"),
                ClassRecord(class_id=200, title="Biology SL", teacher="B", url="https://x/classes/200", raw_hash="h"),
            ]
        )
        TaskRepository(session).upsert_many(
            [
                TaskRecord(task_id=1, class_id=100, title="Overdue essay", due_at=datetime(2026, 9, 8, 9, 0, 0), status="Pending", url="u1", dropbox_url="d1", raw_hash="r"),
                TaskRecord(task_id=2, class_id=100, title="Today quiz", due_at=datetime(2026, 9, 10, 18, 0, 0), status="Pending", url="u2", dropbox_url="d2", raw_hash="r"),
                TaskRecord(task_id=3, class_id=200, title="Next week lab", due_at=datetime(2026, 9, 15, 9, 0, 0), status="Pending", url="u3", dropbox_url="d3", raw_hash="r"),
                TaskRecord(task_id=4, class_id=200, title="No due", due_at=None, status="Pending", url="u4", dropbox_url="d4", raw_hash="r"),
            ]
        )

    upcoming = read.agenda(view="upcoming", now=now)
    ids = [t["task_id"] for t in upcoming.data["tasks"]]
    assert ids == [2, 3]  # sorted by due, overdue + no-due excluded
    assert upcoming.data["tasks"][0]["class_name"] == "Mathematics HL"
    assert upcoming.data["tasks"][0]["due_relative"] == "in 6 hours"

    overdue = read.agenda(view="overdue", now=now)
    assert [t["task_id"] for t in overdue.data["tasks"]] == [1]

    today = read.agenda(view="today", now=now)
    assert [t["task_id"] for t in today.data["tasks"]] == [2]

    week = read.agenda(view="week", now=now)
    assert [t["task_id"] for t in week.data["tasks"]] == [2, 3]

    math = read.agenda(view="all", subject="math", now=now)
    assert {t["task_id"] for t in math.data["tasks"]} == {1, 2}

    window = read.agenda(within_days=3, now=now)
    assert [t["task_id"] for t in window.data["tasks"]] == [2]


def test_scrape_deadlines_walks_pages_and_stops():
    """Pagination must gather every page yet stop as soon as a page adds nothing."""
    import pathlib

    from managebac_mcp.browser import PlaywrightBrowserGateway
    from managebac_mcp.config import load_managebac_config

    config = load_managebac_config(pathlib.Path("config/managebac.yaml"))

    class FakePage:
        def __init__(self):
            self.urls: list[str] = []

        def goto(self, url, timeout=None):
            self.urls.append(url)

        def wait_for_timeout(self, ms):
            pass

    def task(task_id: int) -> TaskRecord:
        return TaskRecord(
            task_id=task_id, class_id=1, title=f"T{task_id}", due_at=None,
            status=None, url="u", dropbox_url="d", raw_hash="r",
        )

    # Two full pages, then a page that only repeats what we already have.
    pages = {1: [task(1), task(2)], 2: [task(3)], 3: [task(3)]}

    class Gateway(PlaywrightBrowserGateway):
        def _parse_deadline_tiles(self, page, direction=None):
            return pages.get(len(page.urls), [])

    gw = Gateway(config)
    page = FakePage()
    records = gw._scrape_deadlines(page, "overdue")

    assert sorted(r.task_id for r in records) == [1, 2, 3]
    # Stopped on the repeat page rather than walking to the cap.
    assert len(page.urls) == 3
    assert "page=" not in page.urls[0]
    assert page.urls[1].endswith("&page=2")


def test_scrape_deadlines_stops_when_page_param_ignored():
    """If the site ignored ?page, the identical second page must end the loop."""
    import pathlib

    from managebac_mcp.browser import PlaywrightBrowserGateway
    from managebac_mcp.config import load_managebac_config

    config = load_managebac_config(pathlib.Path("config/managebac.yaml"))

    class FakePage:
        def __init__(self):
            self.urls: list[str] = []

        def goto(self, url, timeout=None):
            self.urls.append(url)

        def wait_for_timeout(self, ms):
            pass

    same = [
        TaskRecord(task_id=7, class_id=1, title="T", due_at=None, status=None,
                   url="u", dropbox_url="d", raw_hash="r")
    ]

    class Gateway(PlaywrightBrowserGateway):
        def _parse_deadline_tiles(self, page, direction=None):
            return list(same)

    gw = Gateway(config)
    page = FakePage()
    records = gw._scrape_deadlines(page, "upcoming")

    assert [r.task_id for r in records] == [7]
    assert len(page.urls) == 2  # page 1, then the duplicate page 2 stops it


def test_parse_time_range_handles_noon_and_midday():
    from managebac_mcp.browser import PlaywrightBrowserGateway as G

    assert G._parse_time_range("8:40 AM - 9:40 AM") == ("08:40", "09:40")
    assert G._parse_time_range("12:10 PM - 1:10 PM") == ("12:10", "13:10")
    assert G._parse_time_range("11:05 AM - 12:05 PM") == ("11:05", "12:05")
    assert G._parse_time_range("") == (None, None)


def test_schedule_groups_by_day_and_joins_same_day_deadlines():
    from managebac_mcp.repositories import ClassRepository, LessonRepository, TaskRepository

    db = build_db()
    read = ReadService(db)

    with db.session() as session:
        ClassRepository(session).upsert_many(
            [ClassRecord(class_id=100, title="Mathematics HL", teacher="A", url="u", raw_hash="h")]
        )
        LessonRepository(session).upsert_many(
            [
                LessonRecord(date="2026-09-07", period="2", class_id=100, title="Maths",
                             grade="DP 2", teacher="A", room="D3", starts_at="09:45",
                             ends_at="10:45", rotation_day="6", raw_hash="r"),
                LessonRecord(date="2026-09-07", period="1", class_id=None, title="Homeroom",
                             grade=None, teacher=None, room=None, starts_at=None,
                             ends_at=None, rotation_day="6", raw_hash="r"),
                LessonRecord(date="2026-09-08", period="1", class_id=100, title="Maths",
                             grade="DP 2", teacher="A", room="D3", starts_at="08:40",
                             ends_at="09:40", rotation_day="7", raw_hash="r"),
            ]
        )
        TaskRepository(session).upsert_many(
            [
                TaskRecord(task_id=1, class_id=100, title="Essay",
                           due_at=datetime(2026, 9, 7, 23, 59), status="Pending",
                           url="u", dropbox_url="d", raw_hash="r"),
                TaskRecord(task_id=2, class_id=100, title="Later",
                           due_at=datetime(2026, 9, 20, 9, 0), status="Pending",
                           url="u", dropbox_url="d", raw_hash="r"),
            ]
        )

    one = read.schedule(date="2026-09-07", days=1)
    assert [d["date"] for d in one.data["days"]] == ["2026-09-07"]
    lessons = one.data["days"][0]["lessons"]
    # Homeroom has no start time, so it sorts first within the day.
    assert [l["title"] for l in lessons] == ["Homeroom", "Maths"]
    assert [t["task_id"] for t in lessons[1]["due_today"]] == [1]
    assert lessons[0]["due_today"] == []

    two = read.schedule(date="2026-09-07", days=2)
    assert [d["date"] for d in two.data["days"]] == ["2026-09-07", "2026-09-08"]
    # The Sep 20 deadline is outside the window and must not leak in.
    assert two.data["days"][1]["lessons"][0]["due_today"] == []
    assert two.data["cached_range"] == {"from": "2026-09-07", "to": "2026-09-08"}


def test_refresh_timetable_anchors_on_monday():
    db = build_db()
    browser = FakeBrowser()
    seen: list[list[str]] = []

    class Recording(FakeBrowser):
        def fetch_timetable(self, start_dates):
            seen.append(start_dates)
            return FakeBrowser.fetch_timetable(self, start_dates)

    sync = SyncService(db, Recording())
    # 2026-09-10 is a Thursday; the week must start on Monday the 7th.
    res = sync.refresh_timetable(start_date="2026-09-10", weeks=2)
    assert res.success
    assert seen == [["2026-09-07", "2026-09-14"]]


def test_refresh_deadlines_skips_the_past_view_and_upserts():
    db = build_db()
    seen: list = []

    class Recording(FakeBrowser):
        def fetch_deadlines(self, views=None, max_pages=None):
            seen.append(views)
            return FakeBrowser.fetch_deadlines(self, views, max_pages)

    browser = Recording()
    SyncService(db, browser).run_startup_sync()   # seed classes for the FK
    res = SyncService(db, browser).refresh_deadlines()
    assert res.success
    assert res.data["tasks"] == 2
    # The server passes no views, letting the gateway pick the agenda pair.
    assert seen == [None]


def test_agenda_views_constant_excludes_past():
    from managebac_mcp.browser import PlaywrightBrowserGateway as G

    assert G._AGENDA_VIEWS == ("upcoming", "overdue")
    assert "past" in G._DEADLINE_VIEWS


def test_scrape_deadlines_respects_the_page_cap():
    """The interactive path must stop at max_pages even when more pages exist."""
    import pathlib

    from managebac_mcp.browser import PlaywrightBrowserGateway
    from managebac_mcp.config import load_managebac_config

    config = load_managebac_config(pathlib.Path("config/managebac.yaml"))

    class FakePage:
        def __init__(self):
            self.urls: list[str] = []

        def goto(self, url, timeout=None):
            self.urls.append(url)

        def wait_for_selector(self, selector, timeout=None):
            return None

    class Gateway(PlaywrightBrowserGateway):
        def _parse_deadline_tiles(self, page, direction=None):
            n = len(page.urls)
            return [
                TaskRecord(task_id=n, class_id=1, title=f"T{n}", due_at=None, status=None,
                           url="u", dropbox_url="d", raw_hash="r")
            ]

    gw = Gateway(config)
    page = FakePage()
    records = gw._scrape_deadlines(page, "upcoming", max_pages=3)

    # Every page yields something new, so only the cap can stop the walk.
    assert len(page.urls) == 3
    assert sorted(r.task_id for r in records) == [1, 2, 3]


def test_parse_points():
    from managebac_mcp.browser import PlaywrightBrowserGateway as G

    assert G._parse_points("29 / 40 pts") == (29.0, 40.0)
    assert G._parse_points("6 / 7 pts") == (6.0, 7.0)
    assert G._parse_points("") == (None, None)
    assert G._parse_points("Not Assessed Yet") == (None, None)


def test_refresh_grades_is_bounded_and_resumes():
    """A batch stops at the limit, and the next one moves on to unchecked tasks."""
    from managebac_mcp.repositories import ClassRepository, TaskRepository

    db = build_db()
    visited: list[str] = []

    class Grading(FakeBrowser):
        def fetch_task_details(self, task_url: str) -> dict:
            visited.append(task_url)
            return {"grade": "6", "points_earned": 6.0, "points_possible": 7.0,
                    "assessment_status": "Assessed"}

    with db.session() as session:
        ClassRepository(session).upsert_many(
            [ClassRecord(class_id=100, title="Mathematics HL", teacher="A", url="u", raw_hash="h")]
        )
        TaskRepository(session).upsert_many(
            [
                TaskRecord(task_id=i, class_id=100, title=f"Task {i}",
                           due_at=datetime(2026, 8, i + 1, 9, 0), status="Pending",
                           url=f"https://x/task/{i}", dropbox_url="d", raw_hash="r")
                for i in range(1, 6)
            ]
        )

    sync = SyncService(db, Grading())
    now = datetime(2026, 9, 10, 12, 0)

    first = sync.refresh_grades(limit=2, now=now)
    assert first.data["checked"] == 2 and first.data["graded"] == 2
    assert len(visited) == 2

    second = sync.refresh_grades(limit=2, now=now)
    # Already-checked tasks sort last, so the second batch sees different ones.
    assert set(visited[:2]).isdisjoint(set(visited[2:]))

    read = ReadService(db)
    res = read.grades()
    assert len(res.data["grades"]) == 4
    assert res.data["average_by_class"]["Mathematics HL"] == 6.0
    assert res.data["coverage"] == {"tasks_checked": 4, "tasks_total": 5}

    assert read.grades(subject="biology").data["grades"] == []


def test_task_details_live_persists_the_mark():
    from managebac_mcp.repositories import TaskRepository

    db = build_db()

    class Graded(FakeBrowser):
        def fetch_task_details(self, task_url: str) -> dict:
            return {"grade": "5", "points_earned": 29.0, "points_possible": 40.0,
                    "assessment_status": "Assessed"}

    browser = Graded()
    SyncService(db, browser).run_startup_sync()
    task_id = 47417931 + 12816550
    ActionService(db, browser).task_details_live(task_id)

    with db.session() as session:
        row = TaskRepository(session).get(task_id)
        assert (row.grade, row.points_earned, row.points_possible) == ("5", 29.0, 40.0)
        assert row.graded_at is not None


def test_grading_sweep_prefers_submitted_work():
    """Only handed-in work can carry a mark, so it must be checked first."""
    from managebac_mcp.repositories import ClassRepository, TaskRepository

    db = build_db()
    with db.session() as session:
        ClassRepository(session).upsert_many(
            [ClassRecord(class_id=100, title="Maths", teacher="A", url="u", raw_hash="h")]
        )
        TaskRepository(session).upsert_many(
            [
                TaskRecord(task_id=1, class_id=100, title="Old submitted",
                           due_at=datetime(2026, 5, 1, 9, 0), status="Submitted",
                           url="u1", dropbox_url="d", raw_hash="r"),
                TaskRecord(task_id=2, class_id=100, title="Recent pending",
                           due_at=datetime(2026, 9, 1, 9, 0), status="Pending",
                           url="u2", dropbox_url="d", raw_hash="r"),
                TaskRecord(task_id=3, class_id=100, title="Recent unmarked",
                           due_at=datetime(2026, 9, 2, 9, 0), status=None,
                           url="u3", dropbox_url="d", raw_hash="r"),
                TaskRecord(task_id=4, class_id=100, title="Submitted early, due later",
                           due_at=datetime(2027, 1, 5, 9, 0), status="Submitted",
                           url="u4", dropbox_url="d", raw_hash="r"),
                TaskRecord(task_id=5, class_id=100, title="Future, not handed in",
                           due_at=datetime(2027, 2, 5, 9, 0), status="Pending",
                           url="u5", dropbox_url="d", raw_hash="r"),
            ]
        )

    with db.session() as session:
        # Ids read inside the session; the rows detach when it closes.
        order = [r.task_id for r in TaskRepository(session).list_for_grading(10, datetime(2026, 9, 10))]

    # Submitted work leads, newest first; work still to come and never handed in
    # (task 5) is not worth a page load at all.
    assert order == [4, 1, 3, 2]


def test_parse_due_uses_the_list_direction_for_the_missing_year():
    """ManageBac omits the year; the view a tile came from settles which one."""
    from managebac_mcp.browser import PlaywrightBrowserGateway as G

    now = datetime(2026, 9, 3, 12, 0)  # a Thursday, early in the new school year

    # Real cases from the connector's own bad data. ManageBac renders these with
    # a weekday, which is what pinned the correct year down.
    pushkin = G._parse_due("Sep 10, 8:25 AM", now=now, direction="past")
    assert pushkin == datetime(2025, 9, 10, 8, 25)
    assert pushkin.strftime("%A") == "Wednesday"

    series = G._parse_due("Sep 8, 9:50 AM", now=now, direction="past")
    assert series == datetime(2025, 9, 8, 9, 50)
    assert series.strftime("%A") == "Monday"

    # Nearest-year put both a week into the future -- the bug being fixed.
    assert G._parse_due("Sep 10, 8:25 AM", now=now) == datetime(2026, 9, 10, 8, 25)

    # Last term's work is recent enough that both rules agree.
    assert G._parse_due("Jun 24, 8:40 AM", now=now, direction="past") == datetime(2026, 6, 24, 8, 40)

    # A genuine upcoming deadline resolves forward, across a year boundary too.
    assert G._parse_due("Sep 10, 8:25 AM", now=now, direction="future") == datetime(2026, 9, 10, 8, 25)
    assert G._parse_due("Feb 3, 9:00 AM", now=now, direction="future") == datetime(2027, 2, 3, 9, 0)

    # An explicit year always wins, and garbage still yields nothing.
    assert G._parse_due("Jun 23, 2024, 8:25 AM", now=now, direction="future") == datetime(2024, 6, 23, 8, 25)
    assert G._parse_due("not a date", now=now, direction="past") is None
    assert G._parse_due("", now=now, direction="past") is None


def test_every_scraped_view_declares_its_side_of_today():
    from managebac_mcp.browser import PlaywrightBrowserGateway as G

    assert G._DIRECTIONS["upcoming"] == "future"
    assert G._DIRECTIONS["overdue"] == "past"
    assert G._DIRECTIONS["past"] == "past"
    # A new view without a direction would silently fall back to nearest-year.
    assert set(G._DEADLINE_VIEWS) <= set(G._DIRECTIONS)


def test_scrape_deadlines_passes_the_direction_of_its_view():
    import pathlib

    from managebac_mcp.browser import PlaywrightBrowserGateway
    from managebac_mcp.config import load_managebac_config

    config = load_managebac_config(pathlib.Path("config/managebac.yaml"))
    seen: list = []

    class FakePage:
        def __init__(self):
            self.urls: list[str] = []

        def goto(self, url, timeout=None):
            self.urls.append(url)

        def wait_for_selector(self, selector, timeout=None):
            return None

    class Gateway(PlaywrightBrowserGateway):
        def _parse_deadline_tiles(self, page, direction=None):
            seen.append(direction)
            return []

    gw = Gateway(config)
    gw._scrape_deadlines(FakePage(), "past")
    gw._scrape_deadlines(FakePage(), "upcoming")
    assert seen == ["past", "future"]


def test_class_refresh_stores_every_class_it_crawled():
    """One crawl covers all classes, so none of it should be discarded."""
    from managebac_mcp.repositories import TaskRepository

    db = build_db()
    browser = FakeBrowser()
    SyncService(db, browser).run_startup_sync()

    with db.session() as session:
        TaskRepository(session).upsert_many([
            TaskRecord(task_id=47417931 + 12816551, class_id=12816551, title="stale",
                       due_at=None, status=None, url="old", dropbox_url="old", raw_hash="old")
        ])

    res = ActionService(db, browser).refresh_class_tasks(12816550)
    assert res.success
    assert res.data["tasks"] == 1        # the class asked for
    assert res.data["tasks_total"] == 2  # both classes written

    with db.session() as session:
        other = TaskRepository(session).get(47417931 + 12816551)
        assert other.title == "Task for 12816551"  # refreshed, not left stale


def test_agenda_tomorrow_and_month_views():
    from managebac_mcp.repositories import ClassRepository, TaskRepository

    db = build_db()
    read = ReadService(db)
    now = datetime(2026, 9, 10, 12, 0)  # Thursday

    with db.session() as session:
        ClassRepository(session).upsert_many(
            [ClassRecord(class_id=100, title="Maths", teacher="A", url="u", raw_hash="h")]
        )
        TaskRepository(session).upsert_many([
            TaskRecord(task_id=1, class_id=100, title="Later today", due_at=datetime(2026, 9, 10, 18, 0),
                       status=None, url="u", dropbox_url="d", raw_hash="r"),
            TaskRecord(task_id=2, class_id=100, title="Tomorrow early", due_at=datetime(2026, 9, 11, 8, 40),
                       status=None, url="u", dropbox_url="d", raw_hash="r"),
            TaskRecord(task_id=3, class_id=100, title="Tomorrow late", due_at=datetime(2026, 9, 11, 23, 0),
                       status=None, url="u", dropbox_url="d", raw_hash="r"),
            TaskRecord(task_id=4, class_id=100, title="In three weeks", due_at=datetime(2026, 10, 1, 9, 0),
                       status=None, url="u", dropbox_url="d", raw_hash="r"),
            TaskRecord(task_id=5, class_id=100, title="In two months", due_at=datetime(2026, 11, 20, 9, 0),
                       status=None, url="u", dropbox_url="d", raw_hash="r"),
        ])

    assert [t["task_id"] for t in read.agenda(view="today", now=now).data["tasks"]] == [1]
    # A whole calendar day, not a rolling 24 hours.
    assert [t["task_id"] for t in read.agenda(view="tomorrow", now=now).data["tasks"]] == [2, 3]
    assert [t["task_id"] for t in read.agenda(view="week", now=now).data["tasks"]] == [1, 2, 3]
    assert [t["task_id"] for t in read.agenda(view="month", now=now).data["tasks"]] == [1, 2, 3, 4]
    assert [t["task_id"] for t in read.agenda(view="upcoming", now=now).data["tasks"]] == [1, 2, 3, 4, 5]


def test_school_clock_is_used_not_the_server_clock():
    """Deadlines are the school's wall time, so "now" must be too."""
    from datetime import timedelta

    from managebac_mcp.clock import DEFAULT_TIMEZONE, school_now

    assert DEFAULT_TIMEZONE == "Europe/Nicosia"

    nicosia = school_now("Europe/Nicosia")
    tehran = school_now("Asia/Tehran")  # what this server's own clock reads
    assert nicosia.tzinfo is None  # naive, matching how due dates are stored
    assert abs((tehran - nicosia) - timedelta(minutes=30)) < timedelta(seconds=5)

    # An unusable zone must degrade rather than take the connector down.
    assert abs(school_now("Not/AZone") - nicosia) < timedelta(seconds=5)


def _gateway_with_artifacts(artifacts_dir, *, save_on_success=True):
    import pathlib

    from managebac_mcp.browser import PlaywrightBrowserGateway
    from managebac_mcp.config import load_managebac_config

    config = load_managebac_config(pathlib.Path("config/managebac.yaml"))
    config.features.save_artifacts_on_success = save_on_success
    return PlaywrightBrowserGateway(config, pathlib.Path(artifacts_dir))


class _UploadPage:
    """The dropbox page as it behaves after a file has gone up."""

    def __init__(self, body: str):
        self.body = body
        self.uploaded: list[str] = []

    def goto(self, url, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, selector):
        return self.body

    def screenshot(self, path=None, full_page=False):
        Path(path).write_bytes(b"png")

    def content(self):
        return "<html></html>"


def test_a_read_only_artifacts_dir_does_not_fail_a_finished_write(tmp_path):
    """The deployed unit's working directory is read-only under ProtectSystem=strict.

    Evidence-saving must not turn an upload that already reached ManageBac into
    a reported failure -- that is the one direction a caller cannot recover
    from, because retrying submits the same work twice.
    """
    # The suite runs as root, which walks through mode bits, so the refusal is
    # forced by giving the directory an ordinary file as its parent. What is
    # under test is that any OSError from mkdir or write stays contained.
    blocked = tmp_path / "not-a-dir" / "artifacts"
    blocked.parent.write_text("i am a file", encoding="utf-8")

    gw = _gateway_with_artifacts(blocked)
    page = _UploadPage("Homework.pdf uploaded")

    assert gw._save_screenshot(page, "task_upload") is None
    assert gw._save_html(page, "task_upload") is None


def test_artifacts_are_written_where_they_are_configured(tmp_path):
    gw = _gateway_with_artifacts(tmp_path / "arts")
    page = _UploadPage("body")

    shot = gw._save_screenshot(page, "task_upload")
    html = gw._save_html(page, "task_upload")

    assert shot is not None and Path(shot).parent == tmp_path / "arts"
    assert html is not None and Path(html).read_text() == "<html></html>"


def test_save_artifacts_on_success_is_honoured(tmp_path):
    """The flag was declared in config from the start; it must actually gate."""
    gw = _gateway_with_artifacts(tmp_path / "arts", save_on_success=False)
    page = _UploadPage("body")

    assert gw._save_screenshot(page, "task_upload") is None
    assert gw._save_html(page, "task_upload") is None
    assert not (tmp_path / "arts").exists()


def test_upload_is_called_submitted_only_when_managebac_lists_the_file(tmp_path):
    upload = tmp_path / "Homework.pdf"
    upload.write_text("work")

    def outcome_for(body: str):
        gw = _gateway_with_artifacts(tmp_path / "arts")
        page = _UploadPage(body)
        gw._first_locator = lambda p, sel: _StubLocator()
        gw._click_first = lambda p, sel: None
        gw._with_authenticated_browser = lambda run: run(page)
        return gw.submit_task_file("https://example.test/dropbox", upload)

    assert outcome_for("Files: Homework.pdf").status == "submitted"
    assert outcome_for("Add files to this dropbox").status == "unverified"


class _StubLocator:
    def set_input_files(self, path):
        pass

    def click(self):
        pass


def test_service_does_not_report_an_unverified_upload_as_submitted(tmp_path: Path):
    db = build_db()

    class Unverified(FakeBrowser):
        def submit_task_file(self, task_dropbox_url, file_path, comment=None):
            return UploadOutcome(status="unverified", message="Add files to this dropbox")

    browser = Unverified()
    SyncService(db, browser).run_startup_sync()

    file_path = tmp_path / "essay.txt"
    file_path.write_text("done", encoding="utf-8")

    result = ActionService(db, browser).submit_task_file(
        task_id=47417931 + 12816550, file_path=str(file_path)
    )

    assert result.data["status"] == "unverified"
    assert "did not list the file back" in result.message


def test_submission_readiness_reports_a_usable_dropbox(tmp_path: Path):
    db = build_db()

    class Inspecting(FakeBrowser):
        def __init__(self):
            self.seen: list[str] = []

        def inspect_dropbox(self, task_dropbox_url: str) -> dict:
            self.seen.append(task_dropbox_url)
            return {"file_input_found": True, "upload_button_found": True, "page_text": "No files yet"}

    browser = Inspecting()
    SyncService(db, browser).run_startup_sync()

    result = ActionService(db, browser).submission_readiness(47417931 + 12816550)

    assert result.success and result.data["ready"] is True
    assert browser.seen == [result.data["dropbox_url"]]


def test_submission_readiness_refuses_to_call_a_broken_form_ready():
    db = build_db()

    class NoForm(FakeBrowser):
        def inspect_dropbox(self, task_dropbox_url: str) -> dict:
            return {"file_input_found": False, "upload_button_found": False, "page_text": "Submissions are closed"}

    browser = NoForm()
    SyncService(db, browser).run_startup_sync()

    result = ActionService(db, browser).submission_readiness(47417931 + 12816550)

    assert result.data["ready"] is False
    assert "do not submit blind" in result.message


class _Checkbox:
    def __init__(self, box_id: str, checked: bool = False):
        self.id = box_id
        self.checked = checked

    def get_attribute(self, name):
        return self.id if name == "id" else None

    def is_checked(self):
        return self.checked

    def check(self):
        self.checked = True


class _ReflectionFormPage:
    """Just enough of ManageBac's form#new_evidence to drive selection logic.

    The outcomes are real checkboxes with full IB sentences as labels, which is
    why matching has to work on a fragment.
    """

    OUTCOMES = {
        "evidence_learning_outcome_ids_260850": "Undertake challenges that develop new skills",
        "evidence_learning_outcome_ids_260852": "Persevere in action",
        "evidence_learning_outcome_ids_260853": "Working collaboratively with others",
    }

    def __init__(self):
        self.boxes = [_Checkbox(i) for i in self.OUTCOMES]
        self.clicked_tiles: list[str] = []
        self.evidence_type = "JournalEvidence"

    class _List:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, i):
            return self.items[i]

        @property
        def first(self):
            return self.items[0]

    def locator(self, selector: str):
        if "checkbox" in selector:
            return self._List(self.boxes)
        if selector.startswith("label[for="):
            box_id = selector.split("'")[1]
            return self._List([_Label(self.OUTCOMES[box_id])])
        if "f-tile--link" in selector:
            kind = selector.split("'")[1]
            page = self

            class _Tile:
                def count(self_inner):
                    return 1 if kind in ("journal", "file", "video", "website", "photos") else 0

                @property
                def first(self_inner):
                    return self_inner

                def click(self_inner):
                    page.clicked_tiles.append(kind)
                    page.evidence_type = {
                        "journal": "JournalEvidence", "file": "FileEvidence",
                        "video": "YoutubeEvidence", "website": "WebsiteEvidence",
                        "photos": "AlbumEvidence",
                    }[kind]

            return _Tile()
        if selector == "input#evidence_type":
            page = self

            class _Hidden:
                @property
                def first(self_inner):
                    return self_inner

                def get_attribute(self_inner, name):
                    return page.evidence_type

            return _Hidden()
        raise AssertionError(f"unexpected selector {selector}")

    def goto(self, url, timeout=None):
        self.url = url

    def wait_for_timeout(self, ms):
        pass


class _Label:
    def __init__(self, text):
        self.text = text

    def inner_text(self):
        return self.text


def test_outcomes_are_matched_against_their_real_labels():
    """A caller passes a fragment; the labels are full IB sentences."""
    gw = _gateway_with_artifacts("unused")
    page = _ReflectionFormPage()

    chosen = gw._select_outcomes(page, ["persevere", "new skills"])

    assert chosen["outcomes_selected"] == [
        "Persevere in action",
        "Undertake challenges that develop new skills",
    ]
    assert chosen["outcomes_unmatched"] == []
    assert [b.id for b in page.boxes if b.checked] == [
        "evidence_learning_outcome_ids_260850",
        "evidence_learning_outcome_ids_260852",
    ]


def test_an_outcome_that_matches_nothing_is_reported_not_swallowed():
    gw = _gateway_with_artifacts("unused")
    page = _ReflectionFormPage()

    chosen = gw._select_outcomes(page, ["Persevere", "Kindness to badgers"])

    assert chosen["outcomes_selected"] == ["Persevere in action"]
    assert chosen["outcomes_unmatched"] == ["Kindness to badgers"]
    assert len(chosen["outcomes_available"]) == 3


def test_the_reflection_form_is_opened_by_url_and_the_tile_is_verified():
    """Clicking "Add Reflections & Evidence" hits a hidden dropdown item first,
    so the form is reached by its own URL instead."""
    gw = _gateway_with_artifacts("unused")
    page = _ReflectionFormPage()

    gw._open_reflection_form(page, 26692255, "website")

    assert page.url.endswith("/student/ib/activity/cas/26692255/reflections/new")
    assert page.clicked_tiles == ["website"]
    assert page.evidence_type == "WebsiteEvidence"


def test_a_reflection_kind_managebac_does_not_offer_is_refused():
    from managebac_mcp.errors import AppError

    gw = _gateway_with_artifacts("unused")
    page = _ReflectionFormPage()

    with pytest.raises(AppError) as exc:
        gw._open_reflection_form(page, 26692255, "interpretive_dance")
    assert "no 'interpretive_dance' reflection" in str(exc.value)


class _ExperienceFormPage(_ReflectionFormPage):
    """The new-experience form, where the outcomes carry their short names."""

    OUTCOMES = {
        "cas_activity_learning_outcome_ids_260849": "Awareness",
        "cas_activity_learning_outcome_ids_260852": "Perseverance",
    }

    def __init__(self):
        super().__init__()
        self.filled: dict[str, str] = {}
        self.clicked: list[str] = []

    def locator(self, selector: str):
        if "cas_activity[name]" in selector or "cas_activity[notes]" in selector or "input[type='submit']" in selector:
            page = self

            class _Field:
                def count(self_inner):
                    return 1

                @property
                def first(self_inner):
                    return self_inner

                def fill(self_inner, value):
                    page.filled[selector] = value

                def click(self_inner):
                    page.clicked.append(selector)

            return _Field()
        return super().locator(selector)


def test_filling_the_experience_form_is_not_reported_as_creating_one():
    """payload without "submit" fills the form and stops; the caller must be
    able to tell that from an experience that actually exists."""
    gw = _gateway_with_artifacts("unused")
    page = _ExperienceFormPage()
    gw._with_authenticated_browser = lambda run: run(page)

    filled = gw.create_cas_experience({"name": "Beach cleanup", "description": "Weekly"})
    assert filled["saved"] is False
    assert page.clicked == []

    page = _ExperienceFormPage()
    gw._with_authenticated_browser = lambda run: run(page)
    saved = gw.create_cas_experience({"name": "Beach cleanup", "submit": True})
    assert saved["saved"] is True
    assert len(page.clicked) == 1


def test_the_experience_form_is_opened_by_url_not_by_a_menu_item():
    gw = _gateway_with_artifacts("unused")
    page = _ExperienceFormPage()
    gw._with_authenticated_browser = lambda run: run(page)

    gw.create_cas_experience({"name": "Beach cleanup", "outcomes": ["Perseverance"]})

    assert page.url.endswith("/student/ib/activity/cas/new")
