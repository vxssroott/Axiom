import { Repository } from "../engine/types";
import { search as engineSearch } from "../engine/search";
import { getContext as engineContext } from "../engine/context";
import { getDependencies as engineDependencies } from "../engine/dependencies";
import { analyzeImpact as engineImpact } from "../engine/impact";
import { getMemory as engineMemory } from "../engine/memory";

export type MCPResponse<T> = { status: "ok" | "error"; data?: T; evidence?: any[]; error?: string };

export function axiom_search(repo: Repository, input: { query: string; path?: string; limit?: number }): MCPResponse<any> {
  if (!input || typeof input.query !== "string") return { status: "error", error: "Invalid input: query required" };
  const results = engineSearch(repo, input.query, input.limit || 30);
  const evidence = results.map((r) => ({ path: r.path, snippet: r.snippet, score: r.score }));
  return { status: "ok", data: { results }, evidence };
}

export function axiom_context(repo: Repository, input: { target: string; depth?: number }) {
  if (!input || typeof input.target !== "string") return { status: "error", error: "Invalid input: target required" };
  const ctx = engineContext(repo, input.target, input.depth || 1);
  return { status: "ok", data: ctx, evidence: ctx.relatedEntities.flatMap((r: any) => r.evidence || []) };
}

export function axiom_dependencies(repo: Repository, input: { target: string; direction?: "dependencies" | "dependents" | "both"; depth?: number }) {
  if (!input || typeof input.target !== "string") return { status: "error", error: "Invalid input: target required" };
  const deps = engineDependencies(repo, input.target, input.direction || "both", input.depth || 1);
  return { status: "ok", data: deps, evidence: deps.edges };
}

export function axiom_impact(repo: Repository, input: { target: string; proposed_change?: string; depth?: number }) {
  if (!input || typeof input.target !== "string") return { status: "error", error: "Invalid input: target required" };
  const imp = engineImpact(repo, input.target, input.proposed_change, input.depth || 5);
  return { status: "ok", data: imp, evidence: imp.dependencyChains };
}

export function axiom_memory(repo: Repository, input: { query: string; limit?: number }) {
  if (!input || typeof input.query !== "string") return { status: "error", error: "Invalid input: query required" };
  const items = engineMemory(repo, input.query, input.limit || 10);
  return { status: "ok", data: { items }, evidence: items.map((i) => ({ path: i.sourcePath, summary: i.summary })) };
}
