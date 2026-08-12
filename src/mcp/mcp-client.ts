import { createClient } from "@modelcontextprotocol/client";

export async function discoverTools(baseUrl: string) {
  const client = createClient({ baseUrl });
  return await client.listTools();
}

export async function callTool(baseUrl: string, toolName: string, input: any) {
  const client = createClient({ baseUrl });
  return await client.callTool(toolName, input);
}
