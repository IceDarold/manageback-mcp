"""Browser automation contracts and Playwright implementation."""

from __future__ import annotations

import concurrent.futures
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, TypeVar

from .config import ManageBacConfig
from .errors import (
    AppError,
    AUTH_FAILED,
    CAS_REFLECTION_FAILED,
    FILE_NOT_FOUND,
    UNKNOWN_UI_CHANGE,
    UPLOAD_FAILED,
)
from .types import CasExperienceRecord, ClassRecord, TaskRecord


@dataclass
class UploadOutcome:
    status: str
    message: str
    screenshot_path: str | None = None
    html_path: str | None = None


# classes, {class_id: tasks}, cas experiences -- everything one sync collects.
StartupData = tuple[list[ClassRecord], dict[int, list[TaskRecord]], list[CasExperienceRecord]]


class BrowserGateway(Protocol):
    def login(self, username: str, password: str) -> None: ...

    def fetch_classes(self) -> list[ClassRecord]: ...

    def fetch_tasks(self, class_id: int) -> list[TaskRecord]: ...

    def fetch_cas_experiences(self) -> list[CasExperienceRecord]: ...

    def fetch_task_details(self, task_url: str) -> dict: ...

    def collect_startup_data(self) -> "StartupData": ...

    def submit_task_file(self, task_dropbox_url: str, file_path: Path, comment: str | None = None) -> UploadOutcome: ...

    def create_cas_experience(self, payload: dict) -> dict: ...

    def add_cas_reflection_journal(self, experience_id: int, text: str, outcomes: list[str]) -> dict: ...

    def add_cas_reflection_file(self, experience_id: int, file_path: Path, outcomes: list[str]) -> dict: ...

    def add_cas_reflection_link(self, experience_id: int, reflection_type: str, url: str, outcomes: list[str]) -> dict: ...


T = TypeVar("T")


class PlaywrightBrowserGateway:
    """Synchronous Playwright workflow for ManageBac student flows."""

    def __init__(self, config: ManageBacConfig, artifacts_dir: Path = Path("artifacts")):
        self.config = config
        self.artifacts_dir = artifacts_dir

    def _selectors(self, key: str) -> list[str]:
        return self.config.selectors.get(key, [])

    def _hash(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def login(self, username: str, password: str) -> None:
        def _run(page):
            self._perform_login(page, username, password)
            return None

        self._with_browser(_run)

    def _scrape_classes(self, page) -> list[ClassRecord]:
        page.goto(self.config.route_url("classes_index"), timeout=self.config.timeouts_ms.navigation)
        links = page.locator(",".join(self._selectors("classes_cards")))
        records: list[ClassRecord] = []
        for i in range(links.count()):
            href = links.nth(i).get_attribute("href") or ""
            m = re.search(r"/student/classes/(\d+)", href)
            if not m:
                continue
            class_id = int(m.group(1))
            title = links.nth(i).inner_text().strip() or f"Class {class_id}"
            url = self.config.build_url(href)
            records.append(
                ClassRecord(
                    class_id=class_id,
                    title=title,
                    teacher=None,
                    url=url,
                    raw_hash=self._hash(f"{class_id}:{title}:{url}"),
                )
            )
        return dedupe_classes(records)

    def fetch_classes(self) -> list[ClassRecord]:
        return self._with_authenticated_browser(self._scrape_classes)

    # Per-class task lists render nearly empty (a lone task shows only as a nav
    # tab), so the authoritative source is the cross-class Tasks & Deadlines
    # page, whose tiles carry the due date and submission status.
    _DEADLINE_VIEWS = ("upcoming", "overdue", "past")

    @staticmethod
    def _parse_due(text: str) -> "datetime | None":
        text = (text or "").strip()
        if not text:
            return None
        now = datetime.now()
        for fmt in ("%b %d, %Y, %I:%M %p", "%b %d, %I:%M %p", "%b %d"):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            if "%Y" in fmt:
                return parsed
            # Year-less format: choose the year landing the date closest to now
            # (handles both overdue past and upcoming future).
            best = None
            for year in (now.year - 1, now.year, now.year + 1):
                try:
                    candidate = parsed.replace(year=year)
                except ValueError:
                    continue
                if best is None or abs((candidate - now).days) < abs((best - now).days):
                    best = candidate
            return best
        return None

    def _scrape_deadlines(self, page, view: str) -> list[TaskRecord]:
        url = self.config.build_url(self.config.routes.tasks_and_deadlines) + f"?view={view}"
        page.goto(url, timeout=self.config.timeouts_ms.navigation)
        page.wait_for_timeout(500)
        tiles = page.locator("div.f-tile__body")
        records: list[TaskRecord] = []
        for i in range(tiles.count()):
            tile = tiles.nth(i)
            link = tile.locator("a.f-tile__title-link").first
            if link.count() == 0:
                continue
            href = link.get_attribute("href") or ""
            m = re.search(r"/student/classes/(\d+)/core_tasks/(\d+)", href)
            if not m:
                continue
            class_id, task_id = int(m.group(1)), int(m.group(2))
            title = link.inner_text().strip() or f"Task {task_id}"
            due_text = ""
            clock = tile.locator("span:has(svg.fi-clock)").first
            if clock.count() > 0:
                due_text = clock.inner_text().strip()
            status = None
            badge = tile.locator(".badge[data-bs-title]").first
            if badge.count() > 0:
                label = badge.locator(".badge-label").first
                status = ((label.inner_text().strip() if label.count() > 0 else "")
                          or (badge.get_attribute("data-bs-title") or "")).strip() or None
            dropbox_url = self.config.route_url("task_dropbox", class_id=class_id, task_id=task_id)
            raw = f"{class_id}:{task_id}:{title}:{due_text}:{status}"
            records.append(
                TaskRecord(
                    task_id=task_id,
                    class_id=class_id,
                    title=title,
                    due_at=self._parse_due(due_text),
                    status=status,
                    url=self.config.build_url(href),
                    dropbox_url=dropbox_url,
                    raw_hash=self._hash(raw),
                )
            )
        return records

    def _scrape_all_tasks(self, page) -> list[TaskRecord]:
        seen: dict[int, TaskRecord] = {}
        for view in self._DEADLINE_VIEWS:
            for rec in self._scrape_deadlines(page, view):
                seen.setdefault(rec.task_id, rec)
        return list(seen.values())

    def fetch_tasks(self, class_id: int) -> list[TaskRecord]:
        return self._with_authenticated_browser(
            lambda page: [t for t in self._scrape_all_tasks(page) if t.class_id == class_id]
        )

    def collect_startup_data(self) -> "StartupData":
        """Log in once and scrape classes, all tasks, and CAS in one session."""

        def _run(page):
            classes = self._scrape_classes(page)
            tasks_by_class: dict[int, list[TaskRecord]] = {}
            for rec in self._scrape_all_tasks(page):
                tasks_by_class.setdefault(rec.class_id, []).append(rec)
            cas = self._scrape_cas(page)
            return classes, tasks_by_class, cas

        return self._with_authenticated_browser(_run)

    @staticmethod
    def _parse_hours(text: str) -> "float | None":
        m = re.search(r"([\d.]+)\s*hour", text or "", re.I)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    @staticmethod
    def _parse_cas_dates(text: str) -> tuple["str | None", "str | None"]:
        """"Mar 09, 2026 - Mar 21, 2026" -> ("2026-03-09", "2026-03-21")."""
        parsed: list[str] = []
        for raw in re.findall(r"[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}", text or "")[:2]:
            try:
                parsed.append(datetime.strptime(raw, "%b %d, %Y").date().isoformat())
            except ValueError:
                continue
        return (parsed[0] if parsed else None, parsed[1] if len(parsed) > 1 else None)

    def _scrape_cas(self, page) -> list[CasExperienceRecord]:
        page.goto(self.config.route_url("cas_index"), timeout=self.config.timeouts_ms.navigation)
        page.wait_for_timeout(500)

        # Each experience is a card carrying its approval flag, total hours and
        # date range; the bare-link fallback below keeps the sync alive if the
        # card markup changes.
        tiles = page.locator("div.activity-tile")
        records: list[CasExperienceRecord] = []
        for i in range(tiles.count()):
            tile = tiles.nth(i)
            link = tile.locator("h3.title a[href*='/student/ib/activity/cas/']").first
            if link.count() == 0:
                continue
            href = link.get_attribute("href") or ""
            m = re.search(r"/student/ib/activity/cas/(\d+)", href)
            if not m:
                continue
            eid = int(m.group(1))
            title = link.inner_text().strip() or f"CAS {eid}"

            status = None
            flag = tile.locator(".flag-badge[data-bs-title]").first
            if flag.count() > 0:
                status = (flag.get_attribute("data-bs-title") or "").strip() or None

            hours = None
            hours_el = tile.locator("small.hours").first
            if hours_el.count() > 0:
                hours = self._parse_hours(hours_el.inner_text())

            start_date = end_date = None
            calendar = tile.locator(".cas-activity-calendar .cell").first
            if calendar.count() > 0:
                start_date, end_date = self._parse_cas_dates(calendar.inner_text())

            records.append(
                CasExperienceRecord(
                    experience_id=eid,
                    title=title,
                    status=status,
                    start_date=start_date,
                    end_date=end_date,
                    hours=hours,
                    url=self.config.build_url(href),
                    raw_hash=self._hash(f"{eid}:{title}:{status}:{hours}:{start_date}:{end_date}"),
                )
            )

        if records:
            return dedupe_cas(records)
        return self._scrape_cas_links_only(page)

    def _scrape_cas_links_only(self, page) -> list[CasExperienceRecord]:
        """Fallback: titles and ids only, used when the card markup stops matching."""
        links = page.locator("a[href*='/student/ib/activity/cas/']")
        records: list[CasExperienceRecord] = []
        for i in range(links.count()):
            href = links.nth(i).get_attribute("href") or ""
            m = re.search(r"/student/ib/activity/cas/(\d+)$", href)
            if not m:
                continue
            eid = int(m.group(1))
            title = links.nth(i).inner_text().strip() or f"CAS {eid}"
            url = self.config.build_url(href)
            records.append(
                CasExperienceRecord(
                    experience_id=eid,
                    title=title,
                    status=None,
                    start_date=None,
                    end_date=None,
                    hours=None,
                    url=url,
                    raw_hash=self._hash(f"{eid}:{title}:{url}"),
                )
            )
        return dedupe_cas(records)

    def fetch_task_details(self, task_url: str) -> dict:
        """Live-read one task page: what the student actually has to do."""

        def _run(page):
            page.goto(task_url, timeout=self.config.timeouts_ms.navigation)
            page.wait_for_timeout(800)

            def first_text(selector: str) -> "str | None":
                loc = page.locator(selector).first
                return loc.inner_text().strip() if loc.count() > 0 else None

            def all_texts(selector: str) -> list[str]:
                loc = page.locator(selector)
                return [t for t in (loc.nth(i).inner_text().strip() for i in range(loc.count())) if t]

            return {
                "description": first_text(".core-task-details .fr-view"),
                "labels": all_texts(".label-and-due .label"),
                # HL/SL subject badges sit in the same row, so key off the
                # tooltip attribute the status badge alone carries.
                "status": first_text(".label-and-due .badge[data-bs-title] .badge-label"),
                "due_text": first_text(".due-date .due"),
                "submission_status": first_text(".assessment .cell:not(:has(span.fi__wrapper))"),
                "url": task_url,
            }

        return self._with_authenticated_browser(_run)

    def fetch_cas_experiences(self) -> list[CasExperienceRecord]:
        return self._with_authenticated_browser(self._scrape_cas)

    def submit_task_file(self, task_dropbox_url: str, file_path: Path, comment: str | None = None) -> UploadOutcome:
        if not file_path.exists():
            raise AppError(FILE_NOT_FOUND, f"File does not exist: {file_path}")

        def _run(page):
            page.goto(task_dropbox_url, timeout=self.config.timeouts_ms.navigation)

            file_input = self._first_locator(page, self._selectors("dropbox_file_input"))
            if file_input is None:
                raise AppError(UPLOAD_FAILED, "File input not found on dropbox page")
            file_input.set_input_files(str(file_path))
            self._click_first(page, self._selectors("dropbox_upload_button"))
            page.wait_for_timeout(2000)

            status_text = page.inner_text("body")[:700]
            screenshot = self._save_screenshot(page, "task_upload")
            html = self._save_html(page, "task_upload")
            return UploadOutcome(status="submitted", message=status_text, screenshot_path=screenshot, html_path=html)

        return self._with_authenticated_browser(_run)

    def create_cas_experience(self, payload: dict) -> dict:
        def _run(page):
            page.goto(self.config.route_url("cas_index"), timeout=self.config.timeouts_ms.navigation)
            self._click_first(page, self._selectors("cas_add_experience"))
            page.wait_for_timeout(800)
            if "name" in payload:
                page.get_by_label("Experience Name").fill(payload["name"])
            if "description" in payload:
                page.get_by_label("Description and Goals").fill(payload["description"])
            if payload.get("submit", False):
                page.get_by_role("button", name=re.compile("Add|Save", re.I)).click()
            return {"status": "ok", "screenshot": self._save_screenshot(page, "cas_create_experience"), "html": self._save_html(page, "cas_create_experience")}

        return self._with_authenticated_browser(_run)

    def add_cas_reflection_journal(self, experience_id: int, text: str, outcomes: list[str]) -> dict:
        def _run(page):
            page.goto(self.config.route_url("cas_reflections", experience_id=experience_id), timeout=self.config.timeouts_ms.navigation)
            self._click_first(page, self._selectors("cas_add_reflection"))
            page.get_by_text("Journal", exact=False).first.click()
            page.locator("[contenteditable='true']").first.fill(text)
            self._select_outcomes(page, outcomes)
            page.get_by_role("button", name=re.compile("Add Entry|Save", re.I)).click()
            return {"status": "ok", "screenshot": self._save_screenshot(page, "cas_reflection_journal"), "html": self._save_html(page, "cas_reflection_journal")}

        return self._with_authenticated_browser(_run)

    def add_cas_reflection_file(self, experience_id: int, file_path: Path, outcomes: list[str]) -> dict:
        if not file_path.exists():
            raise AppError(FILE_NOT_FOUND, f"File does not exist: {file_path}")

        def _run(page):
            page.goto(self.config.route_url("cas_reflections", experience_id=experience_id), timeout=self.config.timeouts_ms.navigation)
            self._click_first(page, self._selectors("cas_add_reflection"))
            page.get_by_text("File", exact=False).first.click()
            page.locator("input[type='file']").first.set_input_files(str(file_path))
            self._select_outcomes(page, outcomes)
            page.get_by_role("button", name=re.compile("Add Entry|Save", re.I)).click()
            return {"status": "ok", "screenshot": self._save_screenshot(page, "cas_reflection_file"), "html": self._save_html(page, "cas_reflection_file")}

        return self._with_authenticated_browser(_run)

    def add_cas_reflection_link(self, experience_id: int, reflection_type: str, url: str, outcomes: list[str]) -> dict:
        if reflection_type not in {"video", "website", "photos"}:
            raise AppError(CAS_REFLECTION_FAILED, "reflection_type must be video|website|photos")

        def _run(page):
            page.goto(self.config.route_url("cas_reflections", experience_id=experience_id), timeout=self.config.timeouts_ms.navigation)
            self._click_first(page, self._selectors("cas_add_reflection"))
            page.get_by_text(reflection_type.capitalize(), exact=False).first.click()
            page.locator("input[type='url'], input[placeholder*='http']").first.fill(url)
            self._select_outcomes(page, outcomes)
            page.get_by_role("button", name=re.compile("Add Entry|Save", re.I)).click()
            return {
                "status": "ok",
                "screenshot": self._save_screenshot(page, f"cas_reflection_{reflection_type}"),
                "html": self._save_html(page, f"cas_reflection_{reflection_type}"),
            }

        return self._with_authenticated_browser(_run)

    def _perform_login(self, page, username: str, password: str) -> None:
        page.goto(self.config.build_url(self.config.auth.login_url), timeout=self.config.timeouts_ms.navigation)
        self._fill_first(page, self._selectors("login_username"), username)
        self._fill_first(page, self._selectors("login_password"), password)
        self._click_first(page, self._selectors("login_submit"))
        page.wait_for_timeout(1500)

        # Verify positively: a real student session can open the classes page.
        # Bad credentials leave ManageBac on a login/sign-in page instead, which
        # never contains the "/student" path.
        page.goto(self.config.route_url("classes_index"), timeout=self.config.timeouts_ms.navigation)
        page.wait_for_timeout(500)
        if "/student" not in page.url.lower():
            raise AppError(AUTH_FAILED, "Login failed; ManageBac did not grant a student session (check login/password)")

    def _with_authenticated_browser(self, run: Callable[..., T]) -> T:
        from .credentials import require_credentials

        username, password = require_credentials(self.config)

        def _wrapped(page):
            self._perform_login(page, username, password)
            return run(page)

        return self._with_browser(_wrapped)

    def _with_browser(self, run: Callable[..., T]) -> T:
        def _job() -> T:
            try:
                from playwright.sync_api import sync_playwright
            except Exception as exc:
                raise AppError(AUTH_FAILED, "Playwright is not installed. Install with `pip install .[server]`") from exc

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                try:
                    return run(page)
                finally:
                    context.close()
                    browser.close()

        # The MCP server runs tools inside an asyncio loop, where Playwright's
        # sync API refuses to start. Run the whole browser session in a
        # dedicated worker thread that has no running event loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_job).result()

    def _first_locator(self, page, selectors: list[str]):
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count() > 0:
                return locator.first
        return None

    def _fill_first(self, page, selectors: list[str], value: str) -> None:
        locator = self._first_locator(page, selectors)
        if locator is None:
            raise AppError(UNKNOWN_UI_CHANGE, f"No selector matched for fill: {selectors}")
        locator.fill(value)

    def _click_first(self, page, selectors: list[str]) -> None:
        locator = self._first_locator(page, selectors)
        if locator is None:
            raise AppError(UNKNOWN_UI_CHANGE, f"No selector matched for click: {selectors}")
        locator.click()

    def _save_screenshot(self, page, prefix: str) -> str:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifacts_dir / f"{prefix}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    def _save_html(self, page, prefix: str) -> str:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifacts_dir / f"{prefix}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.html"
        path.write_text(page.content(), encoding="utf-8")
        return str(path)

    def _select_outcomes(self, page, outcomes: list[str]) -> None:
        body = page.inner_text("body")
        for outcome in outcomes:
            if outcome in body:
                page.get_by_text(outcome, exact=False).first.click()


def dedupe_classes(records: list[ClassRecord]) -> list[ClassRecord]:
    return list({r.class_id: r for r in records}.values())


def dedupe_tasks(records: list[TaskRecord]) -> list[TaskRecord]:
    return list({r.task_id: r for r in records}.values())


def dedupe_cas(records: list[CasExperienceRecord]) -> list[CasExperienceRecord]:
    return list({r.experience_id: r for r in records}.values())
