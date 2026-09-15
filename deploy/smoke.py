"""Non-mutating deployed portal and MCP smoke checks. No account credentials needed."""
import asyncio
import os
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from managebac_mcp.config import Settings
from managebac_mcp.managed import COOKIE, ManagedAccounts


async def main():
    for line in Path("/etc/manageback-mcp/auth.env").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os.environ[key] = value
    settings = Settings(managebac_config_path=Path("/opt/manageback-mcp/config/managebac.yaml"))
    managed = ManagedAccounts(settings)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{managed.origin}/settings", cookies={COOKIE: managed.cookie()})
        assert response.status_code == 200
        assert "Ваши аккаунты" in response.text
        response = await client.get(f"{managed.origin}/settings/api/accounts", cookies={COOKIE: managed.cookie()})
        assert response.status_code == 200
        assert "password" not in response.text
        print("External settings page and safe account API: OK")
        response = await client.post(f"{managed.approver}/internal/service-token",
            headers={"Authorization": f"Bearer {managed.secret}"},
            json={"resource": managed.resource, "scopes": ["managebac"], "subject": "owner", "client_id": "managebac-deployment-check"})
        response.raise_for_status()
        token = response.json()["access_token"]
    async with streamablehttp_client(managed.resource, headers={"Authorization": f"Bearer {token}"}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert len(tools) >= 33
            assert {"read_classes", "read_agenda", "read_schedule", "list_accounts", "disconnect_account"} <= {t.name for t in tools}
            assert all("password" not in t.inputSchema.get("properties", {}) for t in tools)
            result = await session.call_tool("list_accounts", {})
            assert not result.isError
            print(f"Resource-scoped MCP OAuth and discovery: OK ({len(tools)} tools)")


if __name__ == "__main__":
    asyncio.run(main())
