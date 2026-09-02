"""MCP server wiring for ManageBac tools."""

from __future__ import annotations

from typing import Any

from .config import Settings, load_managebac_config
from .credentials import parse_basic_auth, require_credentials, set_resolver
from .db import Database
from .services import ActionService, ReadService, SyncService
from .types import ToolResult


def _serialize(result: ToolResult) -> dict[str, Any]:
    return result.to_dict()


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
        return _serialize(read_service.auth_status())

    @mcp.tool(name="action_login", annotations=_RO)
    def action_login() -> dict[str, Any]:
        username, password = require_credentials(cfg)
        return _serialize(action_service.login(username, password))

    @mcp.tool(name="action_startup_sync", annotations=_WR)
    def action_startup_sync() -> dict[str, Any]:
        return _serialize(sync_service.run_startup_sync())

    @mcp.tool(name="read_classes", annotations=_RO)
    def read_classes() -> dict[str, Any]:
        return _serialize(read_service.list_classes())

    @mcp.tool(name="action_refresh_classes", annotations=_WR)
    def action_refresh_classes() -> dict[str, Any]:
        return _serialize(action_service.refresh_classes())

    @mcp.tool(name="read_class_details", annotations=_RO)
    def read_class_details(class_id: int) -> dict[str, Any]:
        return _serialize(read_service.class_details(class_id))

    @mcp.tool(name="read_class_tasks", annotations=_RO)
    def read_class_tasks(class_id: int) -> dict[str, Any]:
        return _serialize(read_service.class_tasks(class_id))

    @mcp.tool(name="action_refresh_class_tasks", annotations=_WR)
    def action_refresh_class_tasks(class_id: int) -> dict[str, Any]:
        return _serialize(action_service.refresh_class_tasks(class_id))

    @mcp.tool(name="read_task", annotations=_RO)
    def read_task(task_id: int) -> dict[str, Any]:
        return _serialize(read_service.task_details(task_id))

    @mcp.tool(name="read_task_dropbox", annotations=_RO)
    def read_task_dropbox(task_id: int) -> dict[str, Any]:
        return _serialize(read_service.task_dropbox(task_id))

    @mcp.tool(name="action_submit_task_file", annotations=_WR)
    def action_submit_task_file(task_id: int, file_path: str, comment: str | None = None) -> dict[str, Any]:
        return _serialize(action_service.submit_task_file(task_id=task_id, file_path=file_path, comment=comment))

    @mcp.tool(name="read_submission_result", annotations=_RO)
    def read_submission_result(task_id: int) -> dict[str, Any]:
        return _serialize(read_service.submission_result(task_id))

    @mcp.tool(name="action_retry_submission", annotations=_WR)
    def action_retry_submission(task_id: int, file_path: str) -> dict[str, Any]:
        return _serialize(action_service.retry_submission(task_id=task_id, file_path=file_path))

    @mcp.tool(name="read_cas_dashboard", annotations=_RO)
    def read_cas_dashboard() -> dict[str, Any]:
        return _serialize(read_service.cas_dashboard())

    @mcp.tool(name="action_refresh_cas", annotations=_WR)
    def action_refresh_cas() -> dict[str, Any]:
        return _serialize(action_service.refresh_cas())

    @mcp.tool(name="read_cas_experience", annotations=_RO)
    def read_cas_experience(experience_id: int) -> dict[str, Any]:
        return _serialize(read_service.cas_experience(experience_id))

    @mcp.tool(name="action_create_cas_experience", annotations=_WR)
    def action_create_cas_experience(payload: dict[str, Any]) -> dict[str, Any]:
        return _serialize(action_service.create_cas_experience(payload))

    @mcp.tool(name="read_cas_reflections", annotations=_RO)
    def read_cas_reflections(experience_id: int) -> dict[str, Any]:
        return _serialize(read_service.cas_reflections(experience_id))

    @mcp.tool(name="action_add_reflection_journal", annotations=_WR)
    def action_add_reflection_journal(experience_id: int, text: str, outcomes: list[str]) -> dict[str, Any]:
        return _serialize(action_service.add_reflection_journal(experience_id=experience_id, text=text, outcomes=outcomes))

    @mcp.tool(name="action_add_reflection_file", annotations=_WR)
    def action_add_reflection_file(experience_id: int, file_path: str, outcomes: list[str]) -> dict[str, Any]:
        return _serialize(action_service.add_reflection_file(experience_id=experience_id, file_path=file_path, outcomes=outcomes))

    @mcp.tool(name="action_add_reflection_video", annotations=_WR)
    def action_add_reflection_video(experience_id: int, video_url: str, outcomes: list[str]) -> dict[str, Any]:
        return _serialize(
            action_service.add_reflection_link(experience_id=experience_id, reflection_type="video", url=video_url, outcomes=outcomes)
        )

    @mcp.tool(name="action_add_reflection_website", annotations=_WR)
    def action_add_reflection_website(experience_id: int, website_url: str, outcomes: list[str]) -> dict[str, Any]:
        return _serialize(
            action_service.add_reflection_link(experience_id=experience_id, reflection_type="website", url=website_url, outcomes=outcomes)
        )

    @mcp.tool(name="action_add_reflection_photos", annotations=_WR)
    def action_add_reflection_photos(experience_id: int, photos_url: str, outcomes: list[str]) -> dict[str, Any]:
        return _serialize(
            action_service.add_reflection_link(experience_id=experience_id, reflection_type="photos", url=photos_url, outcomes=outcomes)
        )

    return mcp
