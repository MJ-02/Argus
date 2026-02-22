// ─── Backend API response types (mirror Pydantic schemas) ────────────────────

export interface APIPaper {
  id: string;
  title: string | null;
  abstract: string | null;
  publication_year: number | null;
  doi: string | null;
  citation_count: number;
  source: string | null;
  created_at: string;
  updated_at: string;
}

export interface APIPaperPage {
  total: number;
  limit: number;
  offset: number;
  items: APIPaper[];
}

export interface APIAuthor {
  id: string;
  name: string | null;
  orcid: string | null;
  works_count: number;
  citation_count: number;
  created_at: string;
  updated_at: string;
}

export interface APICitationNode {
  id: string;
  title: string | null;
  publication_year: number | null;
  citation_count: number | null;
}

export interface APICitationEdge {
  source: string;
  target: string;
}

export interface APICitationGraph {
  nodes: APICitationNode[];
  edges: APICitationEdge[];
}

// ─── Frontend display types ───────────────────────────────────────────────────

export interface Paper {
  id: string;
  title: string;
  abstract: string;
  year: number | null;
  doi: string | null;
  citation_count: number;
  source: string | null;
  authors: { id: string; name: string }[];
  venue?: string;
  topics: string[];
  references?: number;
  coauthors?: number;
}

export interface Author {
  id: string;
  name: string;
  orcid: string | null;
  works_count: number;
  citation_count: number;
  institution?: string;
  topics: string[];
  coauthors: { id: string; name: string }[];
}

export interface GraphNode {
  id: string;
  label: string;
  type: "paper" | "author" | "topic" | "institution";
  year?: number;
  citations?: number;
  x: number;
  y: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: "citation" | "authored" | "has_topic" | "affiliated";
}
