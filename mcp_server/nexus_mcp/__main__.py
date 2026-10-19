"""python -m nexus_mcp — start the NEXUS MCP server on stdio (normally spawned by the backend)."""

from __future__ import annotations

import asyncio

import mcp.server.stdio

from .server import build_server, make_service


async def main() -> None:
    server = build_server(make_service())
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
