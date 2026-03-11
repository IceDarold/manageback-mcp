"""Browser automation contracts and Playwright implementation."""

from __future__ import annotations

import hashlib
import os
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


class BrowserGateway(Protocol):
    def login(self, username: str, password: str) -> None: ...

    def fetch_classes(self) -> list[ClassRecord]: ...

    def fetch_tasks(self, class_id: int) -> list[TaskRecord]: ...

    def fetch_cas_experiences(self) -> list[CasExperienceRecord]: ...

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

    def fetch_classes(self) -> list[ClassRecord]:
        def _run(page):
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

        return self._with_authenticated_browser(_run)

    def fetch_tasks(self, class_id: int) -> list[TaskRecord]:
        def _run(page):
            page.goto(self.config.route_url("class_tasks", class_id=class_id), timeout=self.config.timeouts_ms.navigation)
            links = page.locator(",".join(self._selectors("task_links")))
            records: list[TaskRecord] = []
            for i in range(links.count()):
                href = links.nth(i).get_attribute("href") or ""
                m = re.search(r"/student/classes/(\d+)/core_tasks/(\d+)", href)
                if not m:
                    continue
                task_id = int(m.group(2))
                title = links.nth(i).inner_text().strip() or f"Task {task_id}"
                url = self.config.build_url(href)
                dropbox_url = self.config.route_url("task_dropbox", class_id=class_id, task_id=task_id)
                raw = f"{class_id}:{task_id}:{title}:{url}:{dropbox_url}"
                records.append(
                    TaskRecord(
                        task_id=task_id,
                        class_id=class_id,
                        title=title,
                        due_at=None,
                        status=None,
                        url=url,
                        dropbox_url=dropbox_url,
                        raw_hash=self._hash(raw),
                    )
                )
            return dedupe_tasks(records)

        return self._with_authenticated_browser(_run)

    def fetch_cas_experiences(self) -> list[CasExperienceRecord]:
        def _run(page):
            page.goto(self.config.route_url("cas_index"), timeout=self.config.timeouts_ms.navigation)
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

        return self._with_authenticated_browser(_run)

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
        page.wait_for_timeout(1000)

        if "login" in page.url and "/student" not in page.url:
            raise AppError(AUTH_FAILED, "Login failed; still on login page")

    def _with_authenticated_browser(self, run: Callable[..., T]) -> T:
        username = os.getenv(self.config.auth.username_env)
        password = os.getenv(self.config.auth.password_env)
        if not username or not password:
            raise AppError(AUTH_FAILED, "Credentials are not set in environment")

        def _wrapped(page):
            self._perform_login(page, username, password)
            return run(page)

        return self._with_browser(_wrapped)

    def _with_browser(self, run: Callable[..., T]) -> T:
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
