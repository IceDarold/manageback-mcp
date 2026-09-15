"""Migrate the exact old ManageBac installation to server-owned credentials.

Run as lifeos-platform. Dry-run by default; --apply commits. No credentials are
printed. Only the owner connection at the retired first-party endpoint is in scope.
"""
import argparse
import asyncio
import copy
import json
import os
import site
import sys
import time
from pathlib import Path

# This host has independent environments for the platform and external server.
site.addsitedir("/opt/lifeos/green/.venv/lib/python3.12/site-packages")
sys.path.insert(0, "/opt/lifeos/green/src")

from sqlalchemy import select
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from managebac_mcp.config import load_managebac_config
from lifeos_platform.database import Database
from lifeos_platform.models import ConnectorDefinition, ConnectorInstallation, ExternalConnection, User
from lifeos_platform.secrets import EncryptedFileStore
from managebac_mcp.managed import AccountVault

OLD_URL = "https://mcp.archik.tech/manageback/mcp"


async def migrate(database, secrets_store, vault, template, *, school, catalog=None, apply=False):
    migrated = 0
    prepared_count = 0
    scrub = []
    async with database.sessions() as session:
        rows = (await session.execute(select(ConnectorInstallation, ConnectorDefinition)
            .join(ConnectorDefinition, ConnectorInstallation.definition_id == ConnectorDefinition.id)
            .where(ConnectorInstallation.status == "active", ConnectorDefinition.slug == "manageback",
                ConnectorInstallation.base_url == OLD_URL))).all()
        for installation, definition in rows:
            accounts = (await session.execute(select(ExternalConnection, User)
                .join(User, ExternalConnection.user_id == User.id)
                .where(ExternalConnection.workspace_id == installation.workspace_id,
                    ExternalConnection.provider == f"connector:{installation.id}",
                    ExternalConnection.status == "active"))).all()
            if any(user.external_subject != "owner" for _, user in accounts):
                raise RuntimeError("Owner-only server cannot migrate another user's account")
            duplicate = await session.scalar(select(ConnectorDefinition.id).where(
                ConnectorDefinition.slug == template["slug"],
                ConnectorDefinition.scope_workspace_id == definition.scope_workspace_id,
                ConnectorDefinition.id != definition.id))
            if duplicate:
                raise RuntimeError("A new ManageBac definition already exists; resolve the duplicate explicitly")
            prepared = []
            for connection, user in accounts:
                credentials = secrets_store.read(connection.id, connection.secret_ref).get("credentials", {})
                if not credentials.get("username") or not credentials.get("password"):
                    raise RuntimeError("Legacy account has no usable credentials; reconnect before migration")
                prepared.append((connection, credentials))
            prepared_count += len(prepared)
            if not apply:
                migrated += 1
                continue
            # Persist encrypted destination credentials before changing references.
            # A DB rollback leaves the original source credentials intact.
            for connection, credentials in prepared:
                state = vault.read()
                existing = next((a for a in state["accounts"] if a["username"] == credentials["username"]
                    and a["school"] == school), None)
                account = vault.save({"id": existing["id"] if existing else None,
                    "username": credentials["username"], "password": credentials["password"],
                    "label": connection.metadata_json.get("label") or "Школьный аккаунт",
                    "school": school})
                if not existing:
                    state = vault.read()
                    for entry in state["accounts"]:
                        if entry["id"] == account["id"]:
                            entry["verified_at"] = 0
                    vault.write(state)
                metadata = dict(connection.metadata_json or {})
                metadata.update(identity_verified=False, identity={"account_id": account["id"]},
                    name=account["label"], label=account["label"], connector_installation_id=installation.id)
                connection.metadata_json = metadata
                connection.provider = template["adapter_id"]
                connection.external_subject = account["id"]
                connection.updated_at = time.time()
                scrub.append((connection.id, connection.secret_ref))
            definition.slug = template["slug"]
            definition.name = template["name"]
            definition.description = template["description"]
            definition.category = template["category"]
            definition.icon = template["icon"]
            definition.transport = "mcp"
            definition.manifest = {key: copy.deepcopy(template[key]) for key in (
                "auth", "identity", "connection", "lifecycle", "actions", "open_url",
                "authorization_url", "authorization_return_parameter", "capabilities", "adapter_id")}
            definition.manifest["connection_flow"] = {}
            definition.updated_at = time.time()
            installation.base_url = template["base_url"]
            installation.config = {key: value for key, value in (installation.config or {}).items() if key != "tool_catalog"}
            if catalog is not None:
                installation.config = dict(installation.config, tool_catalog=copy.deepcopy(catalog))
            installation.updated_at = time.time()
            migrated += 1
        if apply:
            await session.commit()
    # After the metadata commit, remove passwords from the source secret files.
    # Keep an encrypted empty credential record for the neutral runtime contract.
    for identifier, reference in scrub:
        secrets_store.write(identifier, {"credentials": {}})
        assert secrets_store.read(identifier, reference) == {"credentials": {}}
    return {"installations": migrated, "accounts_transferred": prepared_count, "applied": apply}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = Database("postgresql+asyncpg://lifeos-platform@/lifeos?host=/var/run/postgresql")
    template = json.loads(Path("/opt/lifeos/green/docs/connectors/managebac.json").read_text())
    school = load_managebac_config(Path("/opt/manageback-mcp/config/managebac.yaml")).base_url
    catalog = None
    if args.apply:
        secret = os.environ.get("APPROVER_INTERNAL_SECRET", "")
        if not secret:
            raise RuntimeError("Run with the service's protected OAuth environment")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{os.environ.get('APPROVER_URL', 'http://127.0.0.1:8102')}/internal/service-token",
                headers={"Authorization": f"Bearer {secret}"}, json={"resource": template["base_url"],
                    "scopes": [template["auth"]["scope"]], "subject": "owner", "client_id": "managebac-migration"})
            response.raise_for_status()
            token = response.json()["access_token"]
        async with streamablehttp_client(template["base_url"], headers={"Authorization": f"Bearer {token}"}) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                internal = {a["upstream_name"] for a in template["actions"] if a.get("internal")}
                catalog = [{"id": t.name, "upstream_name": t.name, "title": t.title or t.name,
                    "description": t.description or "", "input_schema": t.inputSchema,
                    "output_schema": t.outputSchema, "read_only": bool(t.annotations and t.annotations.readOnlyHint),
                    "destructive": bool(t.annotations and t.annotations.destructiveHint), "internal": t.name in internal}
                    for t in tools]
    try:
        result = await migrate(database, EncryptedFileStore("/var/lib/lifeos-platform"),
            AccountVault(Path("/var/lib/manageback-mcp")), template, school=school, catalog=catalog, apply=args.apply)
        print(json.dumps(result))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
