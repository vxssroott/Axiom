# Axiom MCP Adapter (Prototype)

This document explains the initial MCP adapter built into the Axiom UI repository. It extracts the existing Axiom "engine" (implemented originally in public/axiom.html) into reusable TypeScript modules and exposes a simple agent-facing interface.

What Axiom provides to coding agents
- Repository ingestion (from arrays of {path,content})
- Parsing (language detection, import extraction)
- Tokenization (simple token heuristics)
- Dependency graph construction
- Semantic/token search (term overlap)
- Health/risk analysis (heuristic)
- Impact analysis (dependents traversal)
- Local engineering memory discovery (heuristic ADR/README extraction)

Current architecture
- The UI (public/axiom.html) continues to perform client-side ingestion by fetching repositories and zips in the browser. That behavior is unchanged.
- The extracted engine lives under `src/engine/` and offers a programmatic API so the same logic can be run in Node/Bun or a dedicated MCP server in future.
- The MCP tool wrappers live under `src/mcp/` and provide the five initial read-only tools: axiom_search, axiom_context, axiom_dependencies, axiom_impact, axiom_memory.

How the MCP layer interacts with the engine
- The MCP tool functions accept a `Repository` instance produced by the engine (for now, the repo is created by the UI or tests and supplied to the MCP layer).
- The MCP wrappers are thin: they validate input, call engine functions, and return concise structured responses with evidence.

Current limitation: Axiom memory is local/browser-based
- The canonical engineering memory in this repository is per-user and lives in the browser's `localStorage` (key `axiom.repo.v2`). There is no server-side vector DB or knowledge graph in this repo.
- The extracted engine does not invent or create a server-side memory store. It simply enables the same analysis to be run in other environments.

How to run the engine outside the UI
1. Build a `Repository` by calling `ingestCodebase(name, files)` where `files` is an array of `{path, content}`.
2. Call APIs exported from `src/engine`, e.g. `search(repo, "authentication")`, `getContext(repo, "src/auth/login.ts")`.

How an external agent such as Drevin can consume it
- Short-term: run a separate process that imports `src/engine` (or compiles it to JS) and expose a read-only HTTP/MCP surface that calls the engine functions against a given Repository instance.
- The MCP adapter included here (`src/mcp`) provides tools that operate against an explicitly supplied Repository instance. The next step is to host a small server that wires these tools to an HTTP or RPC transport and wires a real repository storage backend.

Future path toward hosted/shared Axiom memory
- Host a canonical service that persists repositories and computed graphs (e.g., a Node service with a vector DB and graph store). The MCP adapter should be implemented there as a thin, read-only layer delegating to the canonical services.
- Do NOT duplicate vector DBs or invent a second canonical store.

Security considerations
- The engine respects file exclusion heuristics from the UI (e.g., skip node_modules, .git). The MCP surface must also enforce not returning secrets such as .env files, private keys, tokens, or credentials.
- The current implementation does not introduce auth. Any future hosted adapter must enforce tenant/project permissions.

This prototype is intentionally conservative: it extracts the working client-side logic into reusable modules without changing the existing UI behavior.
