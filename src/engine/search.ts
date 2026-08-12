import { Repository, SearchResult } from "./types";

export function search(repo: Repository, query: string, limit = 30): SearchResult[] {
  const terms = (query || "").toLowerCase().match(/[a-z_][a-z0-9_]{2,}/g) || [];
  if (!terms.length) return [];
  const scored = repo.files
    .map((f) => {
      let s = 0;
      for (const t of terms) {
        if (f.path.toLowerCase().includes(t)) s += 8;
        if (f.module.toLowerCase().includes(t)) s += 4;
        const tc = f.tokens.filter((x) => x === t).length;
        s += tc * 1;
        if ((f.content || "").toLowerCase().includes(t)) s += 2;
      }
      return { f, s };
    })
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, limit);

  return scored.map(({ f, s }) => ({ path: f.path, snippet: snippet(f.content || "", terms), score: s, lang: f.lang }));
}

export function snippet(src: string, terms: string[]) {
  if (!src) return undefined;
  const s = src.toLowerCase();
  let idx = -1;
  for (const t of terms) {
    const i = s.indexOf(t);
    if (i >= 0 && (idx < 0 || i < idx)) idx = i;
  }
  if (idx < 0) return undefined;
  const start = Math.max(0, idx - 120);
  const end = Math.min(src.length, idx + 240);
  return src.slice(start, end).trim();
}
