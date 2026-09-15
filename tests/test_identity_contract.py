from mcp.server.fastmcp import FastMCP

from managebac_mcp.config import Settings
from managebac_mcp.managed import ManagedAccounts
from managebac_mcp.server import ConnectionIdentity


def test_managed_identity_is_not_wrapped_in_result(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGEBAC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPROVER_INTERNAL_SECRET", "test-secret")
    managed = ManagedAccounts(Settings(managebac_config_path="config/managebac.example.yaml"))
    mcp = FastMCP("identity-contract")

    @managed.tool(mcp, name="whoami")
    def identity() -> ConnectionIdentity:
        return ConnectionIdentity(id="opaque-account-id", name="Student", verified=True)

    tool = mcp._tool_manager._tools["whoami"]
    assert "id" in tool.output_schema["properties"]
    assert "result" not in tool.output_schema["properties"]
    _, structured = tool.fn_metadata.convert_result(identity.__wrapped__())
    assert structured["id"] == "opaque-account-id"
    assert structured["verified"] is True
