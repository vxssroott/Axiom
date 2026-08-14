import http from "node:http";
import { randomUUID } from "node:crypto";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

import { loadRepoFromPath } from "./server";
import { Repository } from "../engine/types";
import * as tools from "./tools";

const SERVER_INFO = { name: "axiom", version: "1.0.0" };

// Input schemas are real zod objects at runtime (validation + tools/list JSON
// schema). The `as any` only sidesteps the SDK's zod-compat generics, which
// trigger TS2589 (excessively deep type instantiation) with this zod version.
const SearchInput = z.object({
  query: z.string(),
  path: z.string().optional(),
  limit: z.number().optional(),
});
const ContextInput = z.object({
  target: z.string(),
  depth: z.number().optional(),
});
const DependenciesInput = z.object({
  target: z.string(),
  direction: z.enum(["dependencies", "dependents", "both"]).optional(),
  depth: z.number().optional(),
});
const ImpactInput = z.object({
  target: z.string(),
  proposed_change: z.string().optional(),
  depth: z.number().optional(),
});
const MemoryInput = z.object({
  query: z.string(),
  limit: z.number().optional(),
});

function textResult(payload: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(payload) }] };
}

/**
 * Builds an MCP server instance with the five Axiom tools registered.
 * One instance is needed per connected transport (the SDK's Protocol allows a
 * single transport per server instance).
 */
async function buildServer(repo: Repository) {
  const server = new McpServer(SERVER_INFO);

  server.registerTool(
    "axiom_search",
    {
      title: "Search the indexed codebase",
      description:
        "Semantic/token search across the indexed codebase. Returns ranked file matches with scores, snippets, and language.",
      inputSchema: SearchInput as any,
    },
    async (args: any) => textResult(tools.axiom_search(repo, args)),
  );

  server.registerTool(
    "axiom_context",
    {
      title: "Retrieve grounded engineering context",
      description:
        "Returns imports, dependents, and related entities for a target file path or symbol, with evidence snippets.",
      inputSchema: ContextInput as any,
    },
    async (args: any) => textResult(tools.axiom_context(repo, args)),
  );

  server.registerTool(
    "axiom_dependencies",
    {
      title: "Understand dependency relationships",
      description:
        "Returns dependency nodes and edges for a target file, optionally filtered by direction (dependencies/dependents/both) and depth.",
      inputSchema: DependenciesInput as any,
    },
    async (args: any) => textResult(tools.axiom_dependencies(repo, args)),
  );

  server.registerTool(
    "axiom_impact",
    {
      title: "Determine affected components from a change",
      description:
        "Walks the reverse dependency graph from a target file to estimate blast radius, affected files, dependency chains, and a low/medium/high risk estimate.",
      inputSchema: ImpactInput as any,
    },
    async (args: any) => textResult(tools.axiom_impact(repo, args)),
  );

  server.registerTool(
    "axiom_memory",
    {
      title: "Retrieve engineering memory",
      description:
        "Returns ADRs, design docs, READMEs, and decision/convention files relevant to a query — the codebase constitution.",
      inputSchema: MemoryInput as any,
    },
    async (args: any) => textResult(tools.axiom_memory(repo, args)),
  );

  return server;
}

/**
 * Loads the repository once and creates a fully wired MCP server (tools
 * registered, ready to connect to a transport). Useful for embedding the
 * server programmatically.
 */
export async function createMCPServer({ repoPath }: { repoPath: string }) {
  const repo = await loadRepoFromPath(repoPath);
  return buildServer(repo);
}

/**
 * Starts an MCP server over the official Streamable HTTP transport.
 * Follows the SDK's session pattern: one transport (and one server instance)
 * per session, keyed by the Mcp-Session-Id header.
 */
export async function startMCPServer({ repoPath, port = 8081 }: { repoPath: string; port?: number }) {
  const repo = await loadRepoFromPath(repoPath);
  const sessions = new Map<string, StreamableHTTPServerTransport>();

  const httpServer = http.createServer(async (req, res) => {
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    let transport = sessionId ? sessions.get(sessionId) : undefined;

    if (!transport) {
      // The session ID is only generated when the initialize request arrives, so
      // register the session via onsessioninitialized instead of at construction.
      transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        onsessioninitialized: (sid) => {
          sessions.set(sid, transport!);
          transport!.onclose = () => {
            sessions.delete(sid);
          };
        },
        onsessionclosed: (sid) => {
          sessions.delete(sid);
        },
      });
      const server = await buildServer(repo);
      await server.connect(transport);
    }

    try {
      await transport.handleRequest(req, res);
    } catch (err) {
      console.error("[axiom-mcp] request failed:", err);
      if (!res.headersSent) {
        res.writeHead(500, { "content-type": "application/json" });
        res.end(JSON.stringify({ jsonrpc: "2.0", error: { code: -32603, message: String(err) }, id: null }));
      } else {
        res.end();
      }
    }
  });

  await new Promise<void>((resolve, reject) => {
    httpServer.once("error", reject);
    httpServer.listen(port, resolve);
  });
  const address = httpServer.address();
  const boundPort = typeof address === "object" && address ? address.port : port;

  return {
    httpServer,
    port: boundPort,
    async close() {
      await Promise.allSettled([...sessions.values()].map((t) => t.close()));
      await new Promise<void>((resolve) => httpServer.close(() => resolve()));
    },
  };
}

/**
 * Runs the MCP server over stdio — the primary transport for agent
 * integrations (e.g. `bun src/mcp/mcp-server.ts --repo .`).
 */
export async function startStdioMCPServer({ repoPath }: { repoPath: string }) {
  const server = await buildServer(await loadRepoFromPath(repoPath));
  const transport = new StdioServerTransport();
  await server.connect(transport);
  return { server, transport };
}

// CLI entry
const isCli = process.argv[1]?.endsWith("src/mcp/mcp-server.ts") ?? false;
if (isCli) {
  (async () => {
    const repoArgIndex = process.argv.indexOf("--repo");
    const portArgIndex = process.argv.indexOf("--port");
    const repoPath = repoArgIndex >= 0 ? process.argv[repoArgIndex + 1] : process.env.AXIOM_REPO_PATH;
    const port = portArgIndex >= 0 ? Number(process.argv[portArgIndex + 1]) : undefined;

    if (!repoPath) {
      console.error("Usage: bun src/mcp/mcp-server.ts --repo /path/to/repo [--port 8081]");
      console.error("  (no --port)   MCP over stdio — primary transport for agents");
      console.error("  (--port N)    MCP over Streamable HTTP at http://localhost:N/mcp");
      process.exit(1);
    }

    if (port === undefined) {
      await startStdioMCPServer({ repoPath });
      console.error("[axiom-mcp] stdio server ready");
    } else {
      await startMCPServer({ repoPath, port });
      console.error(`[axiom-mcp] streamable HTTP server listening on http://localhost:${port}/mcp`);
    }
  })();
}
