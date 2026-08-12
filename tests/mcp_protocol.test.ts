import { startMCPServer } from "../src/mcp/mcp-server";
import { ingestCodebase } from "../src/engine/ingest";
import { createClient } from "@modelcontextprotocol/client";

const fixtures = [
  { path: "src/auth/login.ts", content: "export function login(){ /* auth */ }" },
  { path: "src/auth/service.ts", content: "import { login } from './login'\nexport function svc(){ login(); }" },
  { path: "README.md", content: "Project README\nDecision: use JWT" },
];

let server: any;
let baseUrl = "http://localhost:8082";

beforeAll(async () => {
  // write a temp repo directory
  const os = await import('os');
  const fs = await import('fs/promises');
  const path = await import('path');
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'axiom-test-'));
  for (const f of fixtures) {
    const full = path.join(tmp, f.path);
    await fs.mkdir(path.dirname(full), { recursive: true });
    await fs.writeFile(full, f.content, 'utf8');
  }
  server = await startMCPServer({ repoPath: tmp, port: 8082 });
});

afterAll(async () => {
  if (server && server.close) await server.close();
});

test('mcp discovery and axiom_search', async () => {
  const client = createClient({ baseUrl });
  const tools = await client.listTools();
  expect(tools).toContain('axiom_search');
  const resp = await client.callTool('axiom_search', { query: 'auth', limit: 5 });
  expect(resp.status).toBe('ok');
  expect(resp.data.results.length).toBeGreaterThan(0);
});
