import path from "path";
import fs from "fs/promises";
import http from "http";
import { URL } from "url";
import { ingestCodebase } from "../engine/ingest";
import * as tools from "./tools";

const SKIP = /(^|\/)(node_modules|\.git|dist|build|\.next|\.venv|venv|__pycache__|target|vendor|\.turbo|\.cache|coverage)(\/|$)/;
const CODE_EXT = /\.(js|jsx|mjs|cjs|ts|tsx|py|go|rs|java|kt|swift|c|cc|cpp|h|hpp|cs|rb|php|scala|sh|bash|sql|html|css|scss|vue|svelte|yaml|yml|toml|json|md)$/i;

export type MCPServerOptions = {
  repoPath: string;
  port?: number;
};

export async function loadRepoFromPath(repoPath: string) {
  const root = path.resolve(repoPath);
  const files: Array<{ path: string; content: string }> = [];

  async function walk(dir: string) {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const e of entries) {
      const full = path.join(dir, e.name);
      const rel = path.relative(root, full).split(path.sep).join("/");
      if (SKIP.test(rel)) continue;
      if (e.isDirectory()) {
        await walk(full);
      } else if (e.isFile()) {
        if (!CODE_EXT.test(e.name)) continue;
        try {
          const content = await fs.readFile(full, "utf8");
          files.push({ path: rel, content });
        } catch (err) {
          // ignore unreadable files
        }
      }
    }
  }

  await walk(root);
  const repo = ingestCodebase(path.basename(root), files);
  return repo;
}

export function createMCPServer(opts: MCPServerOptions) {
  let repoPromise: Promise<any> | null = null;
  async function initRepo() {
    if (!repoPromise) {
      repoPromise = loadRepoFromPath(opts.repoPath);
    }
    return repoPromise;
  }

  async function listTools() {
    return ["axiom_search", "axiom_context", "axiom_dependencies", "axiom_impact", "axiom_memory"];
  }

  async function handleTool(toolName: string, input: any) {
    const repo = await initRepo();
    switch (toolName) {
      case "axiom_search":
        return tools.axiom_search(repo, input);
      case "axiom_context":
        return tools.axiom_context(repo, input);
      case "axiom_dependencies":
        return tools.axiom_dependencies(repo, input);
      case "axiom_impact":
        return tools.axiom_impact(repo, input);
      case "axiom_memory":
        return tools.axiom_memory(repo, input);
      default:
        return { status: "error", error: "unknown_tool" };
    }
  }

  let server: http.Server | null = null;

  return {
    async start(port = opts.port || 8081) {
      if (server) return;
      server = http.createServer(async (req, res) => {
        try {
          const url = new URL(req.url || "", `http://${req.headers.host}`);
          if (req.method === "GET" && url.pathname === "/mcp/v1/tools") {
            const t = await listTools();
            res.writeHead(200, { "content-type": "application/json" });
            res.end(JSON.stringify({ status: "ok", data: t }));
            return;
          }
          if (req.method === "GET" && url.pathname === "/health") {
            res.writeHead(200, { "content-type": "application/json" });
            res.end(JSON.stringify({ status: "ok" }));
            return;
          }
          const toolMatch = url.pathname.match(/^\/mcp\/v1\/tools\/(.+)$/);
          if (req.method === "POST" && toolMatch) {
            const toolName = decodeURIComponent(toolMatch[1]);
            let body = "";
            for await (const chunk of req) body += chunk;
            let json: any = {};
            try {
              json = body ? JSON.parse(body) : {};
            } catch (err) {
              res.writeHead(400, { "content-type": "application/json" });
              res.end(JSON.stringify({ status: "error", error: "invalid_json" }));
              return;
            }
            const result = await handleTool(toolName, json);
            res.writeHead(200, { "content-type": "application/json" });
            res.end(JSON.stringify(result));
            return;
          }
          res.writeHead(404, { "content-type": "application/json" });
          res.end(JSON.stringify({ status: "error", error: "not_found" }));
        } catch (err) {
          res.writeHead(500, { "content-type": "application/json" });
          res.end(JSON.stringify({ status: "error", error: String(err) }));
        }
      });
      await new Promise<void>((resolve) => server!.listen(port, resolve));
      return server;
    },
    async stop() {
      if (!server) return;
      await new Promise<void>((resolve, reject) => server!.close((err) => (err ? reject(err) : resolve())));
      server = null;
    },
    async listTools() {
      return listTools();
    },
    async handleTool(toolName: string, input: any) {
      return handleTool(toolName, input);
    },
    async repo() {
      return initRepo();
    },
  };
}

// CLI entry
if (process.argv[1] && process.argv[1].endsWith("src/mcp/server.ts")) {
  (async () => {
    const portArgIndex = process.argv.indexOf("--port");
    const repoArgIndex = process.argv.indexOf("--repo");
    const port = portArgIndex >= 0 ? Number(process.argv[portArgIndex + 1]) : Number(process.env.AXIOM_MCP_PORT || 8081);
    const repoPath = repoArgIndex >= 0 ? process.argv[repoArgIndex + 1] : process.env.AXIOM_REPO_PATH;
    if (!repoPath) {
      console.error("Usage: bun src/mcp/server.ts --repo /path/to/repo [--port 8081]");
      process.exit(1);
    }
    const s = createMCPServer({ repoPath, port });
    await s.start(port);
    console.log(`Axiom MCP server listening on http://localhost:${port}`);
  })();
}
