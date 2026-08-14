import { File, Repository } from "./types";

export function buildGraph(files: File[]) {
  const byPath: Record<string, File> = {};
  files.forEach((f) => (byPath[f.path] = f));
  const edges: [string, string][] = [];
  for (const f of files) {
    for (const imp of f.imports) {
      if (!imp.startsWith(".")) continue;
      const base = f.path.split("/");
      base.pop();
      const parts = (base.join("/") + "/" + imp).split("/");
      // Resolve .. and .
      const stack: string[] = [];
      for (const p of parts) {
        if (p === "..") stack.pop();
        else if (p !== "." && p !== "") stack.push(p);
      }
      const resolved = stack.join("/");
      const cand = [resolved, resolved + ".ts", resolved + ".js", resolved + ".tsx", resolved + "/index.ts", resolved + "/index.js"];
      const hit = cand.find((c) => !!byPath[c]);
      if (hit) edges.push([f.path, hit]);
    }
  }
  const modules: Record<string, { name: string; files: number; lines: number; langs: Record<string, number> }> = {};
  for (const f of files) {
    modules[f.module] ||= { name: f.module, files: 0, lines: 0, langs: {} };
    modules[f.module].files++;
    modules[f.module].lines += f.lines;
    modules[f.module].langs[f.lang] = (modules[f.module].langs[f.lang] || 0) + 1;
  }
  const fanIn: Record<string, number> = {};
  edges.forEach(([a, b]) => (fanIn[b] = (fanIn[b] || 0) + 1));
  files.forEach((f) => {
    const fi = fanIn[f.path] || 0;
    f.fanIn = fi;
    f.fanOut = f.imports.filter((i) => i.startsWith(".")).length;
    f.risk = Math.min(100, Math.round(fi * 15 + (f.lines > 400 ? 20 : 0) + (/auth|payment|billing|crypto|secret/i.test(f.path) ? 25 : 0)));
  });
  return { edges, modules, fanIn };
}

export function computeHealth(files: File[], edges: [string, string][]) {
  const avgLines = Math.round(files.reduce((s, f) => s + f.lines, 0) / Math.max(1, files.length));
  const highRisk = files.filter((f) => (f.risk || 0) >= 60).length;
  const orphans = files.filter((f) => !edges.find((e) => e[0] === f.path || e[1] === f.path)).length;
  const score = Math.max(0, Math.round(100 - highRisk * 2 - (avgLines > 200 ? 15 : 0) - Math.min(20, (orphans / Math.max(1, files.length)) * 40)));
  return { score, avgLines, highRisk, orphans };
}

export function buildRepository(name: string, files: File[]) {
  const { edges, modules } = buildGraph(files);
  const health = computeHealth(files, edges);
  return { name, files, edges, modules, health, createdAt: Date.now() } as Repository;
}
