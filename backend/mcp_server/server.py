"""
MCP server exposing Digarr's playlist import/search functionality to MCP clients
(e.g. Claude Desktop) running on the same machine as the Digarr container.

Runs as a separate process from the main FastAPI app (invoked via `docker exec`),
sharing tool implementations with http_server.py via tools.py. See tools.py for
why this is safe to call directly rather than going over HTTP.

Launch (from the container, CWD=/app):
    python -m mcp_server.server
"""
from mcp.server.fastmcp import FastMCP

from mcp_server import tools

mcp = FastMCP("digarr")
for _fn in tools.ALL_TOOLS:
    mcp.tool()(_fn)


if __name__ == "__main__":
    mcp.run()
