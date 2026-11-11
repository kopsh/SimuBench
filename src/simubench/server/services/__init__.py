"""The application services both seams share (#894): the HTTP routes (``server/routes``) and the
MCP tools (``mcp_tools``) parse their arguments, call a service, and map the domain exceptions it
raises (``KeyError``, ``ValueError``, ``ClientsConnected``, ``RuntimeError``) to their own errors.
A service never raises ``HTTPException`` or ``ToolError``."""
