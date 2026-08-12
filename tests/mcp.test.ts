import { describe, it, expect } from "vitest";
import { ingestCodebase } from "../src/engine/ingest";
import * as mcp from "../src/mcp/tools";

const fixtures = [
  { path: "src/auth/login.ts", content: "export function login(){ /* auth */ }" },
  { path: "src/auth/service.ts", content: "import { login } from './login'\nexport function svc(){ login(); }" },
  { path: "README.md", content: "Project README\nDecision: use JWT" },
];

describe("mcp tools", () => {
  const repo = ingestCodebase("demo", fixtures);
  it("axiom_search returns results", () => {
    const resp = mcp.axiom_search(repo, { query: "auth" });
    expect(resp.status).toBe("ok");
    expect(resp.data.results.length).toBeGreaterThanOrEqual(1);
  });
  it("axiom_context returns context", () => {
    const resp = mcp.axiom_context(repo, { target: "src/auth/login.ts" });
    expect(resp.status).toBe("ok");
    expect(resp.data.target).toBe("src/auth/login.ts");
  });
  it("axiom_dependencies returns deps", () => {
    const resp = mcp.axiom_dependencies(repo, { target: "src/auth/login.ts" });
    expect(resp.status).toBe("ok");
  });
  it("axiom_impact returns impact", () => {
    const resp = mcp.axiom_impact(repo, { target: "src/auth/login.ts" });
    expect(resp.status).toBe("ok");
  });
  it("axiom_memory returns memory items", () => {
    const resp = mcp.axiom_memory(repo, { query: "auth" });
    expect(resp.status).toBe("ok");
  });
});
