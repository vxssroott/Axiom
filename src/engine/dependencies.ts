import { Repository, DependencyResult } from "./types";

export function getDependencies(repo: Repository, target: string, direction: "dependencies" | "dependents" | "both" = "both", depth = 1): DependencyResult {
  const nodes: Array<{ id: string; path?: string; type?: string }> = [];
  const edges: Array<{ from: string; to: string; type?: string }> = [];
  const fileSet = new Set<string>(repo.files.map((f) => f.path));

  function addNode(p: string) {
    if (!nodes.find((n) => n.id === p)) nodes.push({ id: p, path: p, type: "file" });
  }

  if (fileSet.has(target)) {
    addNode(target);
    if (direction === "dependencies" || direction === "both") {
      for (const [from, to] of repo.edges) {
        if (from === target) {
          addNode(to);
          edges.push({ from, to, type: "import" });
        }
      }
    }
    if (direction === "dependents" || direction === "both") {
      for (const [from, to] of repo.edges) {
        if (to === target) {
          addNode(from);
          edges.push({ from, to, type: "import" });
        }
      }
    }
  } else {
    // target not a path: try to find module or symbol occurrences
    // naive: list any file that mentions the token
    const token = target.toLowerCase();
    for (const f of repo.files) {
      if ((f.content || "").toLowerCase().includes(token)) {
        addNode(f.path);
      }
    }
  }

  return { target, nodes, edges, metadata: {} } as DependencyResult;
}
