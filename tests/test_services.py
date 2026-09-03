from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
        def _parse_deadline_tiles(self, page):
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
        def _parse_deadline_tiles(self, page):
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
