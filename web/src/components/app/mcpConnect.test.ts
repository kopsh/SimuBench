import { describe, expect, it } from "vitest";
import { mcpSnippets } from "./mcpConnect";

describe("mcpSnippets", () => {
  it("gives the address, a client config and the Claude Code command for the same URL", () => {
    const url = "http://127.0.0.1:8765/mcp";
    const [plain, json, claude] = mcpSnippets(url);
    expect(plain).toEqual({ id: "url", value: url });
    expect(JSON.parse(json.value)).toEqual({ mcpServers: { simubench: { type: "http", url } } });
    expect(claude.value).toBe(`claude mcp add --transport http simubench ${url}`);
  });
});
