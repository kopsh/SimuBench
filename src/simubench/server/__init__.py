#: Where ``serve --mcp`` answers MCP (streamable HTTP, #1188). Here, not in ``app``, so that the CLI
#: can print the address without importing the whole web app (#1199).
MCP_PATH = "/mcp"
