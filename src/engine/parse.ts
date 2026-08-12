import { File } from "./types";

const CODE_EXT = /\.(js|jsx|mjs|cjs|ts|tsx|py|go|rs|java|kt|swift|c|cc|cpp|h|hpp|cs|rb|php|scala|sh|bash|sql|html|css|scss|vue|svelte|yaml|yml|toml|json|md)$/i;

export function detectLang(path: string): string {
  const e = (path.split(".").pop() || "").toLowerCase();
  const map: Record<string, string> = {
    js: "JavaScript",
    jsx: "JavaScript",
    mjs: "JavaScript",
    cjs: "JavaScript",
    ts: "TypeScript",
    tsx: "TypeScript",
    py: "Python",
    go: "Go",
    rs: "Rust",
    java: "Java",
    kt: "Kotlin",
    swift: "Swift",
    rb: "Ruby",
    php: "PHP",
    scala: "Scala",
    html: "HTML",
    css: "CSS",
    scss: "SCSS",
    vue: "Vue",
    svelte: "Svelte",
    json: "JSON",
    md: "Markdown",
    sql: "SQL",
    sh: "Shell",
  };
  return map[e] || "Unknown";
}

export function moduleOf(path: string) {
  const parts = path.split("/").filter((x) => x && x !== "src" && x !== "app" && x !== "lib");
  if (parts.length <= 1) return "root";
  return parts.slice(0, Math.min(2, parts.length - 1)).join("/");
}

export function extractImports(src: string, lang: string) {
  const out = new Set<string>();
  try {
    const re1 = /(?:from\s+['"]([^'"]+)['"])|(?:require\(\s*['"]([^'"]+)['"]\s*\))|(?:import\s+['"]([^'"]+)['"])/g;
    const re2 = /^\s*import\s+([^\s;]+)/gm; // python/java loose
    let m: RegExpExecArray | null;
    while ((m = re1.exec(src))) out.add(m[1] || m[2] || m[3]);
    while ((m = re2.exec(src))) out.add(m[1]);
  } catch (e) {
    // ignore parse errors
  }
  return [...out].filter((x) => x && !x.startsWith("http"));
}

export function tokenize(s: string) {
  if (!s) return [];
  return (s.toLowerCase().match(/[a-z_][a-z0-9_]{2,}/g) || []).slice(0, 500);
}

export function mkFile(path: string, content: string | null): File {
  const lang = detectLang(path);
  const lines = content ? content.split("\n").length : 0;
  const imports = content ? extractImports(content, lang) : [];
  return { path, content, lang, module: moduleOf(path), lines, size: content ? content.length : 0, imports, tokens: tokenize(content || "") };
}

export function isCodePath(path: string) {
  return CODE_EXT.test(path);
}
