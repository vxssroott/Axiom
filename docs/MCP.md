# Axiom MCP Adapter

Axiom exposes its codebase-analysis engine as an MCP (Model Context Protocol) server built on the official [`@modelcontextprotocol/sdk`](https://github.com/modelcontextprotocol/typescript-sdk). It lets MCP-compatible coding agents (e.g. Drevin, Claude, Cursor — see [AGENTS.md](./AGENTS.md) for the full agent list and per-agent configuration) query an indexed repository for search, context, dependencies, impact, and engineering memory.

What Axiom provides to coding agents
- Repository ingestion (from arrays of `{path, content}`, or from disk via `loadRepoFromPath`)
- Parsing (language detection, import extraction)
- Tokenization (simple token heuristics)
- Dependency graph construction
- Semantic/token search (term overlap)
- Health/risk analysis (heuristic)
- Impact analysis (dependents traversal)
- Local engineering memory discovery (heuristic ADR/README extraction)

Architecture
- The UI (`public/axiom.html`) continues to perform client-side ingestion in the browser; that behavior is unchanged.
- The extracted engine lives under `src/engine/` and is used programmatically.
- The MCP layer lives under `src/mcp/`:
  - `mcp-server.ts` — the real MCP server. Registers five tools on an official `McpServer` and supports two transports:
    - **stdio** (primary): `bun src/mcp/mcp-server.ts --repo /path/to/repo` (or `bun run mcp:start`)
    - **Streamable HTTP** (debug/remote): `bun src/mcp/mcp-server.ts --repo /path/to/repo --port 8081` (or `bun run mcp:http`) → endpoint `http://localhost:8081/mcp`
  - `mcp-client.ts` — small official-SDK client helpers (`discoverTools`, `callTool`) for smoke-testing an endpoint.
  - `server.ts` — legacy debug-only JSON-over-HTTP transport (NOT the MCP protocol). Kept for manual smoke tests only; do not integrate agents against it.
  - `tools.ts` — the five tool implementations (thin wrappers over `src/engine`).

The five tools
- `axiom_search` — ranked semantic/token search across the indexed codebase
- `axiom_context` — imports, dependents, and related entities for a target file or symbol
- `axiom_dependencies` — dependency nodes/edges for a target, by direction and depth
- `axiom_impact` — blast radius and low/medium/high risk estimate for a change to a target
- `axiom_memory` — ADRs, READMEs, and decision/convention documents relevant to a query

How to run the engine outside the UI
1. Build a `Repository` by calling `ingestCodebase(name, files)` where `files` is an array of `{path, content}`.
2. Call APIs exported from `src/engine`, e.g. `search(repo, "authentication")`, `getContext(repo, "src/auth/login.ts")`.

How an external agent consumes Axiom
- **Recommended:** launch the server over stdio and speak the MCP protocol:
  ```sh
  bun src/mcp/mcp-server.ts --repo /path/to/repo
  ```
- Alternatively connect over Streamable HTTP to `http://localhost:8081/mcp` with an MCP client (any transport-compatible client; `src/mcp/mcp-client.ts` shows the official SDK usage).
- Per-agent configuration (Claude Desktop/Code, Cursor, GitHub Copilot, Cline, Roo Code, Continue, OpenCode, Windsurf, Zed, and generic SDK clients) is documented in [AGENTS.md](./AGENTS.md).

Current limitation: Axiom memory is local/browser-based
- The canonical engineering memory in this repository is per-user and lives in the browser's `localStorage` (key `axiom.repo.v2`). There is no server-side vector DB or knowledge graph in this repo.
- The extracted engine does not invent a server-side memory store; it enables the same analysis to run in other environments.

Security considerations
- The engine respects file exclusion heuristics (skips `node_modules`, `.git`, `dist`, etc.).
- The server binds to localhost by default and exposes no authentication — do not expose it publicly. Any future hosted adapter must enforce tenant/project permissions and must not return secrets such as `.env` files, private keys, tokens, or credentials.
