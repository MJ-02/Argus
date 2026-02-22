import type { Metadata } from "next";
import GraphExplorer from "@/components/GraphExplorer";

export const metadata: Metadata = {
  title: "Graph Explorer — Argus",
  description: "Explore citation networks and relationships between research entities.",
};

export default function GraphPage() {
  return <GraphExplorer />;
}
