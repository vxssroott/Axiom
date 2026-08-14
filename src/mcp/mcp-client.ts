import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

/** Connects an official SDK client to an Axiom MCP endpoint (Streamable HTTP). */
export async function createMCPClient(url: string) {
  const transport = new StreamableHTTPClientTransport(new URL(url));
  const client = new Client({ name: "axiom-client", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

/** Lists the tool names exposed by an Axiom MCP endpoint. */
export async function discoverTools(url: string): Promise<string[]> {
  const client = await createMCPClient(url);
  try {
    const { tools } = await client.listTools();
    return tools.map((t) => t.name);
  } finally {
    await client.close();
  }
}

/** Calls a tool on an Axiom MCP endpoint and returns the raw SDK result. */
export async function callTool(url: string, toolName: string, input: Record<string, unknown>) {
  const client = await createMCPClient(url);
  try {
    return await client.callTool({ name: toolName, arguments: input });
  } finally {
    await client.close();
  }
}
