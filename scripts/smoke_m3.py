"""M3 smoke: create_payment + idempotent retry over a real MCP client (demo backend).

Demonstrates the centerpiece end-to-end: same operation_id retried -> one payment, one debit.
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

            args = {
                "payer_account_id": "acct-A",
                "payee_account_id": "acct-B",
                "amount_minor": 500,
                "operation_id": "op-demo",
            }
            first = payload(await session.call_tool("create_payment", args))
            print("create:", first["status"], first["id"])
            assert first["status"] == "SUCCEEDED"

            retry = payload(await session.call_tool("create_payment", args))  # lost-response retry
            print("retry :", retry["status"], retry["id"])
            assert retry["id"] == first["id"], "retry created a second payment!"

            bal = payload(await session.call_tool("get_balance", {"account_id": "acct-A"}))
            print("acct-A balance:", bal["balance_minor"])
            assert bal["balance_minor"] == 1_000_000 - 500, "debited more than once!"

            over = payload(
                await session.call_tool(
                    "create_payment",
                    {"payer_account_id": "acct-A", "payee_account_id": "acct-B", "amount_minor": 50000},
                )
            )
            print("over-limit ->", over["code"])
            assert over["code"] == "APPROVAL_REQUIRED"

    print("M3 OK")


if __name__ == "__main__":
    asyncio.run(main())
