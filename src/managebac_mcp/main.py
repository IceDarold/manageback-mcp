"""CLI entrypoint."""

from __future__ import annotations

import argparse

from .server import create_mcp_server, create_services


def main() -> None:
    parser = argparse.ArgumentParser(description="ManageBac MCP server")
    parser.add_argument("--sync-only", action="store_true", help="Run startup sync once and exit")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http"],
        help="MCP transport",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3001)
    args = parser.parse_args()

    if args.sync_only:
        _, _, sync_service, _, _ = create_services()
        result = sync_service.run_startup_sync()
        print(result.to_dict())
        return

    mcp = create_mcp_server()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
