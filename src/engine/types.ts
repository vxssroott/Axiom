export type Lang = string;

export interface File {
  path: string;
  content: string | null;
  lang: Lang;
  module: string;
  lines: number;
  size: number;
  imports: string[];
  tokens: string[];
  fanIn?: number;
  fanOut?: number;
  risk?: number;
}

export interface Repository {
  name: string;
  files: File[];
  edges: [string, string][]; // [from, to]
  modules: Record<string, { name: string; files: number; lines: number; langs: Record<string, number> }>;
  health: { score: number; avgLines: number; highRisk: number; orphans: number };
  createdAt?: number;
}

export interface GraphNode {
  id: string;
  files: number;
  lines: number;
  langs: Record<string, number>;
}

export interface GraphEdge {
  from: string;
  to: string;
  type?: string;
}

export interface SearchResult {
  path: string;
  snippet?: string;
  score: number;
  lang?: string;
}

export interface ContextResult {
  target: string;
  relatedEntities: Array<{ name: string; type: string; path?: string; relation?: string; evidence?: Array<{ path: string; snippet?: string }> }>;
}

export interface DependencyResult {
  target: string;
  nodes: Array<{ id: string; path?: string; type?: string }>;
  edges: Array<{ from: string; to: string; type?: string }>;
  metadata?: { cyclesDetected?: boolean };
}

export interface ImpactResult {
  target: string;
  affected: Array<{ path: string; reason: string; distance: number }>;
  dependencyChains: string[][];
  riskEstimate: "low" | "medium" | "high";
}

export interface MemoryItem {
  id: string;
  title: string;
  summary: string;
  sourcePath?: string;
  tags?: string[];
}
