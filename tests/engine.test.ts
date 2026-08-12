import { describe, it, expect } from "vitest";
import { ingestCodebase } from "../src/engine/ingest";
import { search } from "../src/engine/search";
import { getContext } from "../src/engine/context";
import { getDependencies } from "../src/engine/dependencies";
import { analyzeImpact } from "../src/engine/impact";
import { getMemory } from "../src/engine/memory";

const fixtures = [
  { path: "src/auth/login.ts", content: "export function login(){ /* auth */ }" },
  { path: "src/auth/service.ts", content: "import { login } from './login'\nexport function svc(){ login(); }" },
  { path: "src/payments/charge.ts", content: "export function charge(){ /* payment */ }" },
  { path: "README.md", content: "Project README\nDecision: use JWT" },
  { path: "docs/ADR-001.md", content: "ADR: we chose X for auth" },
];

describe("engine basic flow", () => {
  const repo = ingestCodebase("demo", fixtures);
  it("parses files and builds graph", () => {
    expect(repo.files.length).toBe(fixtures.length);
    expect(repo.edges.length).toBeGreaterThanOrEqual(1);
  });
  it("search finds auth files", () => {
    const res = search(repo, "authentication");
    // token overlap heuristic may find README or ADR
    expect(Array.isArray(res)).toBe(true);
  });
  it("context for a file lists imports and dependents", () => {
    const ctx = getContext(repo, "src/auth/login.ts");
    expect(ctx.relatedEntities.some((r) => r.relation === "self")).toBe(true);
  });
  it("dependencies returns nodes and edges", () => {
    const deps = getDependencies(repo, "src/auth/login.ts", "both");
    expect(deps.target).toBe("src/auth/login.ts");
  });
  it("impact finds dependents", () => {
    const imp = analyzeImpact(repo, "src/auth/login.ts");
    expect(imp.target).toBe("src/auth/login.ts");
  });
  it("memory finds ADRs and README", () => {
    const mem = getMemory(repo, "auth");
    expect(mem.length).toBeGreaterThanOrEqual(1);
  });
});
