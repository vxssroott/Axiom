import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import { startMCPServer } from "../src/mcp/mcp-server";

const fixtures = [
  { path: "src/auth/login.ts", content: "export function login(){ /* auth */ }" },
  { path: "src/auth/service.ts", content: "import { login } from './login'\nexport function svc(){ login(); }" },
  { path: "README.md", content: "Project README\nDecision: use JWT" },
];

const EXPECTED_TOOLS = ["axiom_search", "axiom_context", "axiom_dependencies", "axiom_impact", "axiom_memory"];

async function makeFixtureRepo() {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "axiom-test-"));
  for (const f of fixtures) {
    const full = path.join(tmp, f.path);
    await fs.mkdir(path.dirname(full), { recursive: true });
    await fs.writeFile(full, f.content, "utf8");
  }
  return tmp;
}

function readResult(result: any) {
  if (result.structuredContent !== undefined) return result.structuredContent;
  const text = result.content?.find((c: any) => c.type === "text")?.text;
  return text ? JSON.parse(text) : undefined;
}

describe("MCP protocol over Streamable HTTP", () => {
  let repoPath: string;
  let server: Awaited<ReturnType<typeof startMCPServer>>;
  let url: string;

  beforeAll(async () => {
    repoPath = await makeFixtureRepo();
    server = await startMCPServer({ repoPath, port: 0 });
    url = `http://127.0.0.1:${server.port}/mcp`;
  });

  afterAll(async () => {
    if (server) await server.close();
    await fs.rm(repoPath, { recursive: true, force: true });
  });

  it("serves the MCP endpoint over HTTP", async () => {
    // GET without Accept: text/event-stream is rejected by the transport (406),
    // which proves the endpoint is live and speaking the streamable HTTP protocol.
    const res = await fetch(url);
    expect(res.status).toBe(406);
  });

  it("discovers all five tools", async () => {
    const transport = new StreamableHTTPClientTransport(new URL(url));
    const client = new Client({ name: "axiom-e2e", version: "1.0.0" });
    await client.connect(transport);
    try {
      const { tools } = await client.listTools();
      const names = tools.map((t) => t.name);
      for (const name of EXPECTED_TOOLS) {
        expect(names).toContain(name);
      }
    } finally {
      await client.close();
    }
  });

  it("calls axiom_search end-to-end over the MCP protocol", async () => {
    const transport = new StreamableHTTPClientTransport(new URL(url));
    const client = new Client({ name: "axiom-e2e", version: "1.0.0" });
    await client.connect(transport);
    try {
      const result = await client.callTool({ name: "axiom_search", arguments: { query: "auth", limit: 5 } });
      expect(result.isError).toBeFalsy();
      const parsed = readResult(result);
      expect(parsed.status).toBe("ok");
      expect(parsed.data.results.length).toBeGreaterThan(0);
    } finally {
      await client.close();
    }
  });
});
