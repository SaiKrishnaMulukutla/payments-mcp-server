"""M0 smoke test: spawn the MCP server over stdio, list tools, call `ping`.

Verifies the server works end-to-end from a real MCP client — no Node/Inspector needed.
Run:  python scripts/smoke_m0.py
"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "payments_mcp.server"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names)
            assert "ping" in names, f"ping not registered; got {names}"

            result = await session.call_tool("ping", {})
            text = result.content[0].text
            print("ping ->", text)
            assert text == "pong", f"expected 'pong', got {text!r}"

    print("M0 OK")


if __name__ == "__main__":
    asyncio.run(main())
