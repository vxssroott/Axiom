import { Repository, ContextResult } from "./types";

export function getContext(repo: Repository, target: string, depth = 1): ContextResult {
  // target may be a path or symbol; for now, resolve as path if exact match
  const byPath: Record<string, any> = {};
  repo.files.forEach((f) => (byPath[f.path] = f));
  const related: ContextResult["relatedEntities"] = [];
  // If file exists, return imports and dependents
  if (byPath[target]) {
    const file = byPath[target];
    related.push({ name: file.path, type: "file", path: file.path, relation: "self", evidence: [{ path: file.path, snippet: file.content ? file.content.slice(0, 200) : undefined }] });
    // imports
    for (const imp of file.imports) {
      related.push({ name: imp, type: "import", relation: "imports", evidence: [] });
    }
    // dependents: edges where this file is the target
    for (const [from, to] of repo.edges) {
      if (to === file.path) related.push({ name: from, type: "file", path: from, relation: "dependent", evidence: [] });
    }
  } else {
    // fallback: search for files mentioning the symbol
    const terms = (target || "").toLowerCase().split(/[^a-z0-9_]+/).filter(Boolean);
    if (terms.length) {
      repo.files.forEach((f) => {
        const c = (f.content || "").toLowerCase();
        if (terms.some((t) => c.includes(t))) {
          related.push({ name: f.path, type: "file", path: f.path, relation: "mentions", evidence: [{ path: f.path, snippet: (f.content || "").slice(0, 200) }] });
        }
      });
    }
  }
  return { target, relatedEntities: related } as ContextResult;
}
