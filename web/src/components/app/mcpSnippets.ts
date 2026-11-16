/** What an AI assistant needs to connect to this service's MCP endpoint (#1188). */
export type McpSnippet = { id: "url" | "json" | "claude"; value: string };

export function mcpSnippets(url: string): McpSnippet[] {
  return [
    { id: "url", value: url },
    { id: "json", value: JSON.stringify({ mcpServers: { simubench: { type: "http", url } } }, null, 2) },
    { id: "claude", value: `claude mcp add --transport http simubench ${url}` },
  ];
}
