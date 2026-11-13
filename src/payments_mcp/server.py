"""FastMCP entry point.

The MCP tool surface is intentionally empty in this architecture commit. Do not expose
payment or approval operations until their trusted services and tests are implemented.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bharat-connect")


def main() -> None:
    """Run the MCP server using the SDK's configured transport."""
    mcp.run()


if __name__ == "__main__":
    main()
