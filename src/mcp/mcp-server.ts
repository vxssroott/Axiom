import { createServer } from "@modelcontextprotocol/server";
import { z } from "zod";
import * as tools from "./tools";
import { loadRepoFromPath } from "./server";

export async function startMCPServer({ repoPath, port = 8081 }: { repoPath: string; port?: number }) {
  const repo = await loadRepoFromPath(repoPath);

  const server = createServer();

  // Define schemas using zod
  const SearchInput = z.object({ query: z.string(), path: z.string().optional(), limit: z.number().optional() });
  const SearchOutput = z.object({ results: z.array(z.object({ path: z.string(), snippet: z.string().optional(), score: z.number(), lang: z.string().optional() })) });

  const ContextInput = z.object({ target: z.string(), depth: z.number().optional() });
  const ContextOutput = z.object({ target: z.string(), relatedEntities: z.array(z.object({ name: z.string(), type: z.string(), path: z.string().optional(), relation: z.string().optional(), evidence: z.array(z.object({ path: z.string(), snippet: z.string().optional() })).optional() })) });

  const DependenciesInput = z.object({ target: z.string(), direction: z.union([z.literal("dependencies"), z.literal("dependents"), z.literal("both")]).optional(), depth: z.number().optional() });
  const DependenciesOutput = z.object({ target: z.string(), nodes: z.array(z.object({ id: z.string(), path: z.string().optional(), type: z.string().optional() })), edges: z.array(z.object({ from: z.string(), to: z.string(), type: z.string().optional() })), metadata: z.any().optional() });

  const ImpactInput = z.object({ target: z.string(), proposed_change: z.string().optional(), depth: z.number().optional() });
  const ImpactOutput = z.object({ target: z.string(), affected: z.array(z.object({ path: z.string(), reason: z.string(), distance: z.number() })), dependencyChains: z.array(z.array(z.string())), riskEstimate: z.enum(["low", "medium", "high"]) });

  const MemoryInput = z.object({ query: z.string(), limit: z.number().optional() });
  const MemoryOutput = z.object({ items: z.array(z.object({ id: z.string(), title: z.string(), summary: z.string(), sourcePath: z.string().optional(), tags: z.array(z.string()).optional() })) });

  // Register tools
  server.registerTool({
    name: "axiom_search",
    description: "Search the indexed codebase",
    inputSchema: SearchInput,
    outputSchema: SearchOutput,
    handler: async ({ input }: any) => {
      return tools.axiom_search(repo, input);
    },
  });

  server.registerTool({
    name: "axiom_context",
    description: "Retrieve grounded engineering context for a target",
    inputSchema: ContextInput,
    outputSchema: ContextOutput,
    handler: async ({ input }: any) => {
      return tools.axiom_context(repo, input);
    },
  });

  server.registerTool({
    name: "axiom_dependencies",
    description: "Understand dependency relationships",
    inputSchema: DependenciesInput,
    outputSchema: DependenciesOutput,
    handler: async ({ input }: any) => {
      return tools.axiom_dependencies(repo, input);
    },
  });

  server.registerTool({
    name: "axiom_impact",
    description: "Determine affected components from a change",
    inputSchema: ImpactInput,
    outputSchema: ImpactOutput,
    handler: async ({ input }: any) => {
      return tools.axiom_impact(repo, input);
    },
  });

  server.registerTool({
    name: "axiom_memory",
    description: "Retrieve engineering memory and ADRs",
    inputSchema: MemoryInput,
    outputSchema: MemoryOutput,
    handler: async ({ input }: any) => {
      return tools.axiom_memory(repo, input);
    },
  });

  // Start listening
  await server.listen({ port });
  return server;
}

// CLI helper
if (require.main === module) {
  (async () => {
    const repoArgIndex = process.argv.indexOf("--repo");
    const portArgIndex = process.argv.indexOf("--port");
    const repoPath = repoArgIndex >= 0 ? process.argv[repoArgIndex + 1] : process.env.AXIOM_REPO_PATH;
    const port = portArgIndex >= 0 ? Number(process.argv[portArgIndex + 1]) : Number(process.env.AXIOM_MCP_PORT || 8081);
    if (!repoPath) {
      console.error("Usage: node src/mcp/mcp-server.ts --repo /path/to/repo [--port 8081]");
      process.exit(1);
    }
    const server = await startMCPServer({ repoPath, port });
    console.log(`MCP server listening on port ${port}`);
  })();
}
