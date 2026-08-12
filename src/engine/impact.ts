import { Repository, ImpactResult } from "./types";

export function analyzeImpact(repo: Repository, target: string, proposed_change?: string, depth = 5): ImpactResult {
  // BFS over dependency graph to find affected files (dependents)
  const adj: Record<string, string[]> = {};
  repo.edges.forEach(([from, to]) => {
    adj[from] ||= [];
    adj[from].push(to);
    // Also track reverse for dependents
    adj[to] ||= [];
  });

  // If target is a file path, start from there
  const start = target;
  const affected: Array<{ path: string; reason: string; distance: number }> = [];
  const chains: string[][] = [];
  if (!repo.files.find((f) => f.path === start)) {
    return { target, affected: [], dependencyChains: [], riskEstimate: "low" } as ImpactResult;
  }

  // simple BFS that finds files that *depend on* the target (i.e., have edges where to === target)
  // Build reverse adjacency
  const rev: Record<string, string[]> = {};
  repo.edges.forEach(([from, to]) => { rev[to] ||= []; rev[to].push(from); });
  const q: Array<{ path: string; chain: string[] }> = [{ path: start, chain: [start] }];
  const seen = new Set<string>([start]);
  while (q.length) {
    const cur = q.shift()!;
    if (cur.chain.length > depth + 1) continue;
    const parents = rev[cur.path] || [];
    for (const p of parents) {
      if (seen.has(p)) continue;
      seen.add(p);
      const chain = [...cur.chain, p];
      affected.push({ path: p, reason: `dependent of ${cur.path}`, distance: chain.length - 1 });
      chains.push(chain.reverse());
      q.push({ path: p, chain });
    }
  }

  // risk estimate: high if many affected or any affected has high risk
  const highRisk = affected.some((a) => {
    const f = repo.files.find((x) => x.path === a.path);
    return (f?.risk || 0) >= 70;
  });
  const riskEstimate = affected.length > 8 || highRisk ? "high" : affected.length > 2 ? "medium" : "low";

  return { target, affected, dependencyChains: chains, riskEstimate } as ImpactResult;
}
