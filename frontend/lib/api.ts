import type {
  APICitationGraph,
  APIAuthor,
  APIPaper,
  APIPaperPage,
  Author,
  Paper,
} from "@/types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ─── Normalizers ─────────────────────────────────────────────────────────────

function normalizePaper(p: APIPaper): Paper {
  return {
    id: p.id,
    title: p.title ?? "Untitled",
    abstract: p.abstract ?? "",
    year: p.publication_year,
    doi: p.doi,
    citation_count: p.citation_count,
    source: p.source,
    authors: [],
    topics: [],
  };
}

function normalizeAuthor(a: APIAuthor): Author {
  return {
    id: a.id,
    name: a.name ?? "Unknown",
    orcid: a.orcid,
    works_count: a.works_count,
    citation_count: a.citation_count,
    topics: [],
    coauthors: [],
  };
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// ─── Papers ───────────────────────────────────────────────────────────────────

export interface SearchPapersParams {
  q?: string;
  topic?: string;
  year_from?: number;
  year_to?: number;
  limit?: number;
  offset?: number;
}

export async function searchPapers(
  params: SearchPapersParams
): Promise<{ items: Paper[]; total: number } | null> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.topic) qs.set("topic", params.topic);
  if (params.year_from != null) qs.set("year_from", String(params.year_from));
  if (params.year_to != null) qs.set("year_to", String(params.year_to));
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));

  const data = await apiFetch<APIPaperPage>(`/papers/search?${qs}`);
  if (!data) return null;
  return { items: data.items.map(normalizePaper), total: data.total };
}

export async function fetchPaper(id: string): Promise<Paper | null> {
  const data = await apiFetch<APIPaper>(`/papers/${id}`);
  if (!data) return null;
  return normalizePaper(data);
}

export async function fetchCitationGraph(
  id: string,
  depth: number = 1
): Promise<APICitationGraph | null> {
  return apiFetch<APICitationGraph>(`/papers/${id}/citations?depth=${depth}`);
}

// ─── Authors ──────────────────────────────────────────────────────────────────

export async function fetchAuthor(id: string): Promise<Author | null> {
  const data = await apiFetch<APIAuthor>(`/authors/${id}`);
  if (!data) return null;
  return normalizeAuthor(data);
}

export async function fetchAuthorPapers(
  authorId: string,
  limit = 20,
  offset = 0
): Promise<{ items: Paper[]; total: number } | null> {
  const data = await apiFetch<APIPaperPage>(
    `/authors/${authorId}/papers?limit=${limit}&offset=${offset}`
  );
  if (!data) return null;
  return { items: data.items.map(normalizePaper), total: data.total };
}
