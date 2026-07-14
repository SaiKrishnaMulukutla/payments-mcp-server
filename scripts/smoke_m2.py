"""M2 smoke: read tools + policy over a real MCP client (demo backend).

- lists tools (ping + 3 reads)
- get_balance(acct-A) -> 1,000,000 (seeded)
- get_balance(acct-Z) -> ACCOUNT_NOT_ALLOWED (outside the principal's allow-list)
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result) -> dict:
    return json.loads(result.content[0].text)


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "payments_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            names = {t.name for t in (await session.list_tools()).tools}
            print("tools:", sorted(names))
            for t in ("ping", "get_payment", "get_balance", "get_account_ledger"):
                assert t in names, f"missing tool {t}"

            ok = payload(await session.call_tool("get_balance", {"account_id": "acct-A"}))
            print("acct-A balance:", ok)
            assert ok["balance_minor"] == 1_000_000, ok

            denied = payload(await session.call_tool("get_balance", {"account_id": "acct-Z"}))
            print("acct-Z ->", denied)
            assert denied["code"] == "ACCOUNT_NOT_ALLOWED", denied
            assert denied["suggested_action"]

    print("M2 OK")


if __name__ == "__main__":
    asyncio.run(main())
