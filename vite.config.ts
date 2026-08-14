// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - tanstackStart, viteReact, tailwindcss, tsConfigPaths, nitro (build-only using cloudflare as a default target),
//     componentTagger (dev-only), VITE_* env injection, @ path alias, React/TanStack dedupe,
//     error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

export default defineConfig({
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
    // Static export: prerender every route to HTML so the deploy pipeline
    // (which serves static output from dist/) can host the app.
    prerender: { enabled: true },
  },
  // Disable the nitro server bundle for production builds: the Freebuff deploy
  // pipeline serves static files from dist/, and TanStack Start's own build +
  // prerender produces that static output (nitro's layout breaks prerendering).
  nitro: false,
  // Emit the static site directly into dist/ (the deploy pipeline copies dist/*),
  // not into dist/client/. The SSR bundle (dist/server) is only used by the
  // prerender step during the build.
  vite: {
    environments: {
      client: {
        build: { outDir: "dist" },
      },
      // Keep the SSR bundle out of dist/ — it's only needed by the prerender
      // step, and the deploy pipeline copies dist/* to the static host.
      // (TanStack Start's SSR environment is named "ssr".)
      ssr: {
        build: { outDir: "dist-server" },
      },
    },
  },
});
