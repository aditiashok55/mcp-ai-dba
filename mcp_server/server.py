from mcp.server.fastmcp import FastMCP

from mcp_server.tools.connection import check_database_connection
from mcp_server.tools.health import get_database_health


mcp = FastMCP("AI-DBA")


@mcp.tool()
def check_database_connection_tool() -> dict:
    """
    Check whether the PostgreSQL database is reachable.
    """
    return check_database_connection()


@mcp.tool()
def get_database_health_tool() -> dict:
    """
    Retrieve basic PostgreSQL health information.
    """
    return get_database_health()


if __name__ == "__main__":
    mcp.run()