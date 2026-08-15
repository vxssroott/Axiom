# Connecting AI Agents to Axiom (MCP)

> **Axiom is engineering memory infrastructure for AI agents — accessible through the Model Context Protocol.**

Axiom exposes its codebase-analysis engine as an **MCP (Model Context Protocol) server** built on the official
[`@modelcontextprotocol/sdk`](https://github.com/modelcontextprotocol/typescript-sdk). Any MCP-compatible AI agent —
Drevin, Claude, Cursor, Copilot, and others — can connect to the same server and query the repository it indexes.

```
AI Agent (MCP client)  →  MCP protocol  →  Axiom MCP server  →  Axiom engine (src/engine)
                                          (src/mcp/mcp-server.ts)
```

The Axiom side is purely a **server/provider**: it reads a repository from disk at startup, builds the
dependency/knowledge graph, and answers tool calls. It never calls out to a specific agent. There is no
Axiom-side client code and no Drevin-specific (or any other agent-specific) code path — the integration is
identical for every MCP client.

---

## 1. The three integration surfaces

Do not confuse these. Only the first two speak the MCP protocol.

| Surface | Command | Endpoint | Status |
|---|---|---|---|
| **stdio / local MCP** (primary) | `bun src/mcp/mcp-server.ts --repo /path/to/repo` | n/a (JSON-RPC over stdin/stdout) | ✅ MCP — recommended for local agents |
| **Streamable HTTP MCP** (debug/remote) | `bun src/mcp/mcp-server.ts --repo /path/to/repo --port 8081` | `http://localhost:8081/mcp` | ✅ MCP — session-based |
| **Legacy JSON-over-HTTP debug transport** | `bun src/mcp/server.ts --repo /path/to/repo --port 8081` | `http://localhost:8081/mcp/v1/tools` | ⚠️ NOT MCP — debug only |

### 1a. stdio / local MCP (primary)

The agent launches the server as a child process and speaks JSON-RPC over stdin/stdout. One server process is
spawned per client. This is the transport every local coding agent below uses, and it is what the
`mcp:start` npm script wraps:

```sh
# from inside the Axiom repository
bun run mcp:start            # = bun src/mcp/mcp-server.ts --repo ./

# from anywhere, against any repo on disk
bun src/mcp/mcp-server.ts --repo /absolute/path/to/repo
```

`--repo` must point at a real repository on disk — the server indexes **that** path (skipping
`node_modules`, `.git`, `dist`, `build`, `.venv`, `__pycache__`, `target`, `vendor`, `.turbo`, `.cache`,
`coverage`, and any file without a recognized code extension, including `.env`).

### 1b. Streamable HTTP MCP (debug/remote)

```sh
bun run mcp:http     # = bun src/mcp/mcp-server.ts --repo ./ --port 8081
# → MCP endpoint at http://localhost:8081/mcp
```

- Implements the official **Streamable HTTP** transport (JSON-RPC over HTTP; `Mcp-Session-Id` header, one
  server instance per session).
- Useful for debugging (e.g. `src/mcp/mcp-client.ts`, curl, or any HTTP-capable MCP client) and for
  clients that support remote/HTTP MCP servers.
- The server binds to localhost and has **no authentication** — do not expose it publicly.

### 1c. Legacy JSON-over-HTTP debug transport (NOT MCP)

`src/mcp/server.ts` is a simple JSON-over-HTTP surface (`GET /health`, `GET /mcp/v1/tools`,
`POST /mcp/v1/tools/<name>`) kept **only** for manual smoke tests. It does **not** speak the MCP protocol —
do not integrate any agent against it. This is a debug convenience, not an agent interface.

---

## 2. The five Axiom tools

All tools are read-only. Results are contract-shaped JSON (`{ status, data, evidence }`), so agents get
structured, provenance-stamped answers rather than raw file dumps.

| Tool | Inputs | Returns |
|---|---|---|
| `axiom_search` | `query` (required), `path?`, `limit?` | Ranked file matches across the indexed codebase, with scores, snippets, and language |
| `axiom_context` | `target` (required), `depth?` | Imports, dependents, and related entities for a target file path or symbol, with evidence snippets |
| `axiom_dependencies` | `target` (required), `direction?` (`dependencies`/`dependents`/`both`), `depth?` | Dependency nodes and edges for a target |
| `axiom_impact` | `target` (required), `proposed_change?`, `depth?` | Blast radius: affected files, dependency chains, and a low/medium/high risk estimate for changing the target |
| `axiom_memory` | `query` (required), `limit?` | ADRs, READMEs, and decision/convention documents relevant to the query — the codebase "constitution" |

> **Note on memory:** `axiom_memory` returns **repo-derived** engineering memory (decision documents found in
> the repository you point `--repo` at). It is not connected to the per-user browser session memory of the
> standalone UI (`localStorage` key `axiom.repo.v2`) — there is no server-side vector database in this project.

---

## 3. Supported agents

### Compatibility definitions

- **Verified** — exercised end-to-end against this server inside this repository: server startup, `tools/list`
  discovery (all five tools), and a real `callTool` (`axiom_search`) over the MCP protocol with the official
  SDK client (see `tests/mcp_protocol.test.ts`). The stdio transport is the same SDK's `StdioServerTransport`
  wired to the same tool registry.
- **Generic MCP** — the agent documents support for standard MCP servers over the listed transport, using the
  config format shown. The **server side** is protocol-verified; client behavior depends on the agent's own MCP
  client implementation and version, which we have not exercised inside this repository. If a tool does not
  appear after connecting, check that agent's MCP transport/version support (many support stdio today; HTTP
  support is newer and varies).

### Agent matrix

| Agent | Transport(s) | Config location | Compatibility |
|---|---|---|---|
| **Claude** (Desktop / Code) | stdio, HTTP | Desktop: `claude_desktop_config.json` · Code: `claude mcp add` / `.mcp.json` | Verified protocol (MCP reference client) |
| **Cursor** | stdio (HTTP in newer builds) | `~/.cursor/mcp.json` (global) · `.cursor/mcp.json` (project) | Generic MCP |
| **GitHub Copilot** (VS Code / CLI) | stdio | VS Code: `.vscode/mcp.json` · CLI: `~/.copilot/mcp-config.json` | Generic MCP (preview feature) |
| **Cline** | stdio, SSE, HTTP | In-app: Settings → MCP Servers (JSON import) | Generic MCP |
| **Roo Code** | stdio, SSE, HTTP | In-app: MCP settings (JSON import) | Generic MCP |
| **Continue** | stdio (HTTP in newer versions) | `~/.continue/config.json` · workspace `.continue/mcpServers/mcp.json` | Generic MCP |
| **OpenCode** | local (stdio), remote (HTTP) | `opencode.json` → `"mcp"` key | Generic MCP |
| **Windsurf** | stdio | `~/.codeium/windsurf/mcp_config.json` · project `.windsurf/mcp_config.json` | Generic MCP |
| **Zed** | stdio only (remote HTTP needs a local bridge) | `.zed/settings.json` → `"context_servers"` | Generic MCP |
| **Any MCP client (official SDK)** | stdio, Streamable HTTP | code — see §4.10 | **Verified** with the official SDK |

**Drevin** remains a supported integration: it is an MCP-compatible coding agent and connects exactly like the
others (stdio is the recommended path). Nothing in this document changes how Drevin connects.

---

## 4. Copyable configuration examples

All stdio examples use the explicit form `bun src/mcp/mcp-server.ts --repo <path>`. Use an **absolute repo
path** for global (per-machine) configs; use `--repo .` only in project-scoped configs where the client's
working directory is the repo root. The `bun run mcp:start` shorthand is equivalent to
`bun src/mcp/mcp-server.ts --repo ./`.

Requires: [Bun](https://bun.sh) on `PATH` and dependencies installed (`bun install`) in the Axiom repo.

### 4.1 Claude Desktop

File: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows).

```json
{
  "mcpServers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "/absolute/path/to/repo"]
    }
  }
}
```

### 4.2 Claude Code

Add via CLI (stdio):

```sh
claude mcp add axiom -- bun src/mcp/mcp-server.ts --repo /absolute/path/to/repo
```

Or via Streamable HTTP:

```sh
claude mcp add --transport http axiom http://localhost:8081/mcp
```

Or project-scoped `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "."]
    }
  }
}
```

### 4.3 Cursor

File: `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project, checked into the repo).

```json
{
  "mcpServers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "/absolute/path/to/repo"]
    }
  }
}
```

Newer Cursor builds also accept an HTTP server entry (`"url": "http://localhost:8081/mcp"`); stdio above is
the universally supported form.

### 4.4 GitHub Copilot (VS Code)

File: `.vscode/mcp.json` (project-scoped, checked into the repo). Note the top-level key is **`servers`** for
VS Code (vs `mcpServers` for Claude Desktop / Copilot CLI).

```json
{
  "servers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "."]
    }
  }
}
```

MCP tool use in Copilot is a rolling **preview** capability whose availability varies by version/plan — if the
`axiom_*` tools do not surface as chat tools, check Copilot's MCP preview status for your build.

### 4.5 GitHub Copilot (CLI)

File: `~/.copilot/mcp-config.json`. Top-level key is **`mcpServers`**.

```json
{
  "mcpServers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "/absolute/path/to/repo"]
    }
  }
}
```

### 4.6 Cline / Roo Code

In-app: Settings → MCP Servers → configure (or import the JSON below). Both support stdio, SSE, and HTTP
servers; the JSON below is the stdio form.

```json
{
  "mcpServers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "/absolute/path/to/repo"]
    }
  }
}
```

HTTP form (newer Cline/Roo builds): `{ "type": "http", "url": "http://localhost:8081/mcp" }`.

### 4.7 Continue

File: `~/.continue/config.json` (global) or workspace `.continue/mcpServers/mcp.json` (project-scoped).

```json
{
  "mcpServers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "/absolute/path/to/repo"]
    }
  }
}
```

Newer Continue builds also support HTTP MCP servers.

### 4.8 OpenCode

File: `opencode.json` (project root) or `~/.config/opencode/opencode.json` (global). OpenCode uses a
different shape than Claude Desktop — entries live under the `"mcp"` key with a `"type"`.

Local (stdio):

```json
{
  "mcp": {
    "axiom": {
      "type": "local",
      "command": ["bun", "src/mcp/mcp-server.ts", "--repo", "."],
      "enabled": true
    }
  }
}
```

Remote (Streamable HTTP):

```json
{
  "mcp": {
    "axiom": {
      "type": "remote",
      "url": "http://localhost:8081/mcp",
      "enabled": true
    }
  }
}
```

Verify with `opencode mcp list`.

### 4.9 Windsurf

File: `~/.codeium/windsurf/mcp_config.json` (macOS/Linux) or `%USERPROFILE%\.codeium\windsurf\mcp_config.json`
(Windows); project-scoped `.windsurf/mcp_config.json` also works.

```json
{
  "mcpServers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "/absolute/path/to/repo"]
    }
  }
}
```

### 4.10 Zed

File: `.zed/settings.json` (project). Zed keys servers under **`context_servers`** and runs them as stdio child
processes — remote HTTP requires a local bridge, so use stdio.

```json
{
  "context_servers": {
    "axiom": {
      "command": "bun",
      "args": ["src/mcp/mcp-server.ts", "--repo", "/absolute/path/to/repo"]
    }
  }
}
```

(Some Zed versions expect `args` as a single string; if the server fails to start, try
`"args": "src/mcp/mcp-server.ts --repo /absolute/path/to/repo"`.)

### 4.11 Generic MCP clients (official SDK)

Any harness using the official SDK can connect. The repository ships a working client example at
`src/mcp/mcp-client.ts` (`discoverTools`, `callTool`). Streamable HTTP:

```ts
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const transport = new StreamableHTTPClientTransport(new URL("http://localhost:8081/mcp"));
const client = new Client({ name: "my-agent", version: "1.0.0" });
await client.connect(transport);

const { tools } = await client.listTools();                    // → axiom_search, axiom_context, …
const res = await client.callTool({
  name: "axiom_search",
  arguments: { query: "authentication", limit: 5 },
});
```

stdio:

```ts
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const transport = new StdioClientTransport({
  command: "bun",
  args: ["src/mcp/mcp-server.ts", "--repo", "/absolute/path/to/repo"],
});
```

---

## 5. Verifying a connection

- **Automated:** `bun run test` — includes `tests/mcp_protocol.test.ts`, which starts the real server, lists
  all five tools over MCP, and calls `axiom_search` end-to-end.
- **HTTP endpoint liveness:** a plain `GET http://localhost:8081/mcp` returns HTTP `406` (the transport
  requires an MCP `Accept` header) — that 406 is the proof the endpoint is live and speaking Streamable HTTP.
- **After editing a config file:** fully restart the agent (many clients only load MCP servers at startup).

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Tool calls fail with "command not found" or spawn errors | `bun` must be on `PATH` for the client's process; use the absolute `bun` path if needed |
| Tools connect but index the wrong code | `--repo` points at the wrong directory — the server indexes whatever path you pass; use an absolute path |
| No `axiom_*` tools listed after restart | Client's MCP feature is version/plan-gated (e.g. Copilot preview) or the config file is in the wrong location/key |
| HTTP endpoint unreachable | Start with `--port 8081` (or `bun run mcp:http`); the server binds localhost only |
| A tool returns `error` in `status` | Missing required input (e.g. `query` for `axiom_search`, `target` for `axiom_context`) |

## 7. Security

- The MCP server binds to **localhost** and has **no authentication** — do not expose it publicly.
- Ingestion skips dependency/build/vendor directories and files without code extensions, so `.env` and other
  secret files are not indexed by the engine.
- Any future hosted deployment must add authentication, tenant/project scoping, and secret redaction — none of
  that exists today and none is needed for local agent integration.
