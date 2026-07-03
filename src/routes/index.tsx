import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Axiom — Engineering Memory Infrastructure" },
      {
        name: "description",
        content:
          "Axiom is an engineering memory layer that maps codebase relationships, predicts change impact, and preserves institutional knowledge.",
      },
      { property: "og:title", content: "Axiom — Engineering Memory Infrastructure" },
      {
        property: "og:description",
        content:
          "Map codebase relationships, predict change impact, and preserve institutional knowledge.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

function Index() {
  return (
    <iframe
      src={`/axiom.html?v=${Date.now()}`}
      title="Axiom"
      style={{
        border: "none",
        width: "100vw",
        height: "100vh",
        display: "block",
        background: "#0a0a0f",
      }}
    />
  );
}
