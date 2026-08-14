import { mkFile } from "./parse";
import { buildRepository } from "./graph";
import { File, Repository } from "./types";

export function ingestCodebase(name: string, inputs: Array<{ path: string; content: string }>): Repository {
  const files: File[] = inputs.map((f) => mkFile(f.path, f.content));
  return buildRepository(name, files);
}

export function parseFiles(inputs: Array<{ path: string; content: string }>): File[] {
  return inputs.map((f) => mkFile(f.path, f.content));
}

