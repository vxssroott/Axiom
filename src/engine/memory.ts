import { Repository, MemoryItem } from "./types";

export function getMemory(repo: Repository, query: string, limit = 10): MemoryItem[] {
  // Heuristic: find files that look like ADRs, design docs, README, or contain 'decision'/'architecture' tokens
  const q = (query || "").toLowerCase();
  const candidates: MemoryItem[] = [];
  repo.files.forEach((f) => {
    const name = f.path.toLowerCase();
    if (/\b(ad[r]?|design|architecture|decision|readme|convention|constitution)\b/.test(name) || /(adr|architect|decision|runbook|convention|constitution)/i.test(f.content || "")) {
      candidates.push({ id: f.path, title: f.path, summary: (f.content || "").slice(0, 400), sourcePath: f.path, tags: [] });
    } else if (q && (f.content || "").toLowerCase().includes(q)) {
      candidates.push({ id: f.path, title: f.path, summary: (f.content || "").slice(0, 400), sourcePath: f.path, tags: [] });
    }
  });
  return candidates.slice(0, limit);
}
