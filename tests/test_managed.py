import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from managebac_mcp.config import Settings
from managebac_mcp.credentials import require_credentials, set_resolver
from managebac_mcp.errors import AppError
from managebac_mcp.managed import AccountVault, COOKIE, ManagedAccounts, Verifier, school_url, selected_account


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGEBAC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPROVER_INTERNAL_SECRET", "test-secret")
    config = Path(__file__).resolve().parents[1] / "config" / "managebac.example.yaml"
    return ManagedAccounts(Settings(managebac_config_path=config))


def account(label="Student", username="student@example.com"):
    return {"label": label, "username": username, "password": "private-password", "school": "https://school.managebac.com"}


def request(managed, method="GET", origin=None, cookie=True, identifier=None):
    headers = []
    if cookie:
        headers.append((b"cookie", f"{COOKIE}={managed.cookie()}".encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "method": method, "path": "/settings", "headers": headers,
        "scheme": "https", "server": ("managebac.archik.tech", 443), "query_string": b"",
        "path_params": {"identifier": identifier} if identifier else {}})


def test_vault_encrypts_and_separates_accounts(tmp_path):
    vault = AccountVault(tmp_path)
    first = vault.save(account())
    second = vault.save(account("Other", "other@example.com"))
    assert vault.public()["default_id"] == first["id"]
    assert len(vault.public()["accounts"]) == 2
    assert "password" not in str(vault.public())
    assert b"private-password" not in (tmp_path / "accounts.enc").read_bytes()
    assert (tmp_path / "accounts.key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "accounts.enc").stat().st_mode & 0o777 == 0o600
    vault.change(second["id"])
    assert vault.account()["username"] == "other@example.com"
    vault.change(first["id"], remove=True)
    assert vault.account()["id"] == second["id"]
    with pytest.raises(AppError):
        vault.account(first["id"])


def test_reconnect_preserves_id_and_other_accounts(tmp_path):
    vault = AccountVault(tmp_path)
    first = vault.save(account())
    second = vault.save(account("Other"))
    vault.save(dict(first, password="new-password"))
    assert vault.account(first["id"])["password"] == "new-password"
    assert vault.account(second["id"])["password"] == "private-password"
    assert len(vault.public()["accounts"]) == 2
    with pytest.raises(ValueError):
        vault.save(dict(account(), id="unknown"))


@pytest.mark.parametrize("url", ["http://school.managebac.com", "https://evil.com", "https://school.managebac.com.evil.com", "https://u:p@school.managebac.com", "https://school.managebac.com:444", "https://school.managebac.com/path", "https://school.managebac.com?query=1"])
def test_school_address_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        school_url(url)


def test_school_address_accepts_school():
    assert school_url("https://school.managebac.com/") == "https://school.managebac.com"


def test_http_resolver_never_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("LEGACY_LOGIN", "other-student")
    monkeypatch.setenv("LEGACY_PASSWORD", "legacy-secret")
    config = SimpleNamespace(auth=SimpleNamespace(username_env="LEGACY_LOGIN", password_env="LEGACY_PASSWORD"))
    set_resolver(lambda: None)
    try:
        with pytest.raises(AppError):
            require_credentials(config)
    finally:
        set_resolver(None)


def test_settings_authentication_and_csrf(managed):
    assert managed.deny(request(managed, cookie=False)).status_code == 401
    assert managed.deny(request(managed)) is None
    assert managed.deny(request(managed, "POST"), mutation=True).status_code == 403
    assert managed.deny(request(managed, "POST", origin="https://evil.com"), mutation=True).status_code == 403
    assert managed.deny(request(managed, "POST", origin=managed.origin), mutation=True) is None


def test_account_services_use_isolated_databases(managed):
    first = managed.vault.save(account())
    second = managed.vault.save(account("Other"))
    marker = selected_account.set(first)
    try:
        one = managed.services()
        assert managed.credentials() == ("student@example.com", "private-password")
        assert managed.services() is one
        selected_account.set(second)
        two = managed.services()
        assert one[0].engine.url != two[0].engine.url
    finally:
        selected_account.reset(marker)


def test_delete_removes_only_selected_account_cache(managed):
    first = managed.vault.save(account())
    second = managed.vault.save(account("Other"))
    marker = selected_account.set(first)
    try:
        managed.services()
        selected_account.set(second)
        managed.services()
    finally:
        selected_account.reset(marker)
    response = asyncio.run(managed.delete(request(managed, "DELETE", managed.origin, identifier=first["id"])))
    assert response.status_code == 200
    assert not (managed.root / "accounts" / first["id"]).exists()
    assert (managed.root / "accounts" / second["id"]).exists()
    assert managed.vault.account()["id"] == second["id"]


def test_http_tools_have_only_opaque_account_selector(managed, monkeypatch):
    from managebac_mcp.server import create_mcp_server
    monkeypatch.setenv("MANAGEBAC_CONFIG_PATH", str(managed.settings.managebac_config_path))
    server = create_mcp_server(managed_http=True)
    assert "list_accounts" in server._tool_manager._tools
    for name, tool in server._tool_manager._tools.items():
        properties = tool.parameters.get("properties", {})
        assert "password" not in properties
        assert "username" not in properties
        if name not in {"list_accounts", "disconnect_account"}:
            assert "account_id" in properties
    set_resolver(None)


def test_verifier_rejects_wrong_resource_subject_or_scope(managed, monkeypatch):
    payload = {"active": True, "resource": managed.resource, "subject": "owner", "scopes": ["managebac"], "client_id": "test"}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): return httpx.Response(200, json=payload)
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    verifier = Verifier(managed)
    assert asyncio.run(verifier.verify_token("token")) is not None
    payload["resource"] = "https://llm.archik.tech/mcp"
    assert asyncio.run(verifier.verify_token("token")) is None
    payload["resource"] = managed.resource
    payload["subject"] = "another-user"
    assert asyncio.run(verifier.verify_token("token")) is None
    payload["subject"] = "owner"
    payload["scopes"] = []
    assert asyncio.run(verifier.verify_token("token")) is None


def test_account_selection_context_propagates_and_resets(managed, monkeypatch):
    from mcp.server.fastmcp import FastMCP
    import managebac_mcp.managed as module
    first = managed.vault.save(account())
    second = managed.vault.save(account("Other", "other@example.com"))
    monkeypatch.setattr(module, "get_access_token", lambda: SimpleNamespace(subject="owner"))
    mcp = FastMCP("test")
    @managed.tool(mcp, name="read_identity")
    def identity() -> str:
        return managed.credentials()[0]
    assert asyncio.run(identity()) == first["username"]
    assert asyncio.run(identity(account_id=second["id"])) == second["username"]
    assert selected_account.get() is None
    with pytest.raises(AppError):
        asyncio.run(identity(account_id="unregistered"))
    assert selected_account.get() is None


def test_failed_login_does_not_store_credentials(managed, monkeypatch):
    from starlette.testclient import TestClient
    from managebac_mcp.browser import PlaywrightBrowserGateway
    def fail(*args):
        raise AppError("AUTH_FAILED", "sensitive upstream detail")
    monkeypatch.setattr(PlaywrightBrowserGateway, "login", fail)
    with TestClient(managed.mcp().streamable_http_app(), base_url=managed.origin) as client:
        client.cookies.set(COOKIE, managed.cookie())
        response = client.post("/settings/api/accounts", json=account(), headers={"Origin": managed.origin})
    assert response.status_code == 400
    assert "sensitive" not in response.text
    assert managed.vault.public()["accounts"] == []


def test_successful_login_and_reconnect_are_server_owned(managed, monkeypatch):
    from starlette.testclient import TestClient
    from managebac_mcp.browser import PlaywrightBrowserGateway
    logins = []
    monkeypatch.setattr(PlaywrightBrowserGateway, "login", lambda self, username, password: logins.append((username, password)))
    with TestClient(managed.mcp().streamable_http_app(), base_url=managed.origin) as client:
        client.cookies.set(COOKIE, managed.cookie())
        response = client.post("/settings/api/accounts", json=account(), headers={"Origin": managed.origin})
        assert response.status_code == 200
        identifier = response.json()["accounts"][0]["id"]
        assert "private-password" not in response.text
        response = client.post("/settings/api/accounts", json=dict(account(), id=identifier, password="updated-password"), headers={"Origin": managed.origin})
        assert response.status_code == 200
        assert len(response.json()["accounts"]) == 1
    assert logins[-1] == ("student@example.com", "updated-password")
    assert managed.vault.account()["password"] == "updated-password"
