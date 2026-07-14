"""M5 smoke: verify tools, resources, and the prompt all register + the capabilities resource reads."""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "payments_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            print("tools:", sorted(tools))
            for t in ("refund_payment", "check_ledger_integrity"):
                assert t in tools, f"missing {t}"

            resources = [str(r.uri) for r in (await session.list_resources()).resources]
            templates = [
                str(r.uriTemplate) for r in (await session.list_resource_templates()).resourceTemplates
            ]
            print("resources:", resources, "templates:", templates)
            assert any("payments://capabilities" in r for r in resources)
            assert any("payments://payment/" in t for t in templates)

            prompts = {p.name for p in (await session.list_prompts()).prompts}
            print("prompts:", sorted(prompts))
            assert "explain_payment" in prompts

            caps = await session.read_resource("payments://capabilities")
            text = caps.contents[0].text
            print("capabilities:\n", text)
            assert "scopes:" in text

    print("M5 OK")


if __name__ == "__main__":
    asyncio.run(main())
