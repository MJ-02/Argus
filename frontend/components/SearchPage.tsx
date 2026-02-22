"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { searchPapers } from "@/lib/api";
import type { Paper } from "@/types";
import { fmt } from "@/lib/utils";
import Pill from "./Pill";
import { EmptyState, SkeletonPaperCard } from "./Skeleton";

const filterInputStyle: React.CSSProperties = {
  flex: 1,
  padding: "6px 10px",
  border: "1px solid #E5E2DD",
  borderRadius: 8,
  fontSize: 13,
  background: "#FAFAF8",
  outline: "none",
  fontFamily: "var(--font-dm-sans), sans-serif",
  color: "#333",
  width: "100%",
};

const quickActionBtn: React.CSSProperties = {
  padding: "5px 12px",
  borderRadius: 8,
  fontSize: 12,
  fontWeight: 500,
  border: "1px solid #DDD",
  background: "#FFF",
  cursor: "pointer",
  fontFamily: "var(--font-dm-sans), sans-serif",
  color: "#444",
};

export default function SearchPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const query = searchParams.get("q") ?? "";
  const yearFrom = searchParams.get("year_from") ?? "";
  const yearTo = searchParams.get("year_to") ?? "";
  const minCitations = searchParams.get("min_citations") ?? "";
  const sort = searchParams.get("sort") ?? "relevance";
  const page = Number(searchParams.get("page") ?? "1");

  const [results, setResults] = useState<Paper[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState(false);
  const [isDefaultView, setIsDefaultView] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const [localYearFrom, setLocalYearFrom] = useState(yearFrom);
  const [localYearTo, setLocalYearTo] = useState(yearTo);
  const [localMinCit, setLocalMinCit] = useState(minCitations);
  const [localSort, setLocalSort] = useState(sort);

  const LIMIT = 20;
  const abortRef = useRef<AbortController | null>(null);

  const pushFilter = useCallback(
    (updates: Record<string, string>) => {
      const params = new URLSearchParams(searchParams.toString());
      for (const [k, v] of Object.entries(updates)) {
        if (v) params.set(k, v);
        else params.delete(k);
      }
      params.delete("page");
      router.replace(`/?${params.toString()}`);
    },
    [router, searchParams]
  );

  useEffect(() => {
    abortRef.current?.abort();
    setLoading(true);
    setApiError(false);

    const run = async () => {
      if (!query.trim()) {
        // Default view: latest 50 papers
        const data = await searchPapers({ limit: 50, offset: 0 });
        if (data === null) {
          setApiError(true);
          setResults([]);
          setTotal(0);
        } else {
          let items = data.items;
          if (sort === "newest")
            items = [...items].sort((a, b) => (b.year ?? 0) - (a.year ?? 0));
          setResults(items);
          setTotal(data.total);
          setIsDefaultView(true);
        }
        setLoading(false);
        return;
      }

      setIsDefaultView(false);
      const data = await searchPapers({
        q: query,
        year_from: yearFrom ? Number(yearFrom) : undefined,
        year_to: yearTo ? Number(yearTo) : undefined,
        limit: LIMIT,
        offset: (page - 1) * LIMIT,
      });

      if (data === null) {
        setApiError(true);
        setResults([]);
        setTotal(0);
        setLoading(false);
        return;
      }

      let items = data.items;

      if (minCitations)
        items = items.filter((p) => p.citation_count >= Number(minCitations));
      if (sort === "newest")
        items = [...items].sort((a, b) => (b.year ?? 0) - (a.year ?? 0));

      setResults(items);
      setTotal(data.total);
      setLoading(false);
    };

    run();
  }, [query, yearFrom, yearTo, minCitations, sort, page]);

  const totalPages = Math.ceil(total / LIMIT);

  return (
    <div
      style={{
        display: "flex",
        gap: 0,
        maxWidth: 1100,
        margin: "0 auto",
        padding: "88px 24px 48px",
      }}
    >
      {/* Filter panel */}
      <aside
        style={{
          width: filtersOpen ? 220 : 48,
          flexShrink: 0,
          transition: "width 0.2s",
          marginRight: 32,
        }}
      >
        <button
          onClick={() => setFiltersOpen((f) => !f)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            marginBottom: 20,
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "#555",
            fontSize: 13,
            fontWeight: 500,
            fontFamily: "var(--font-dm-sans), sans-serif",
          }}
        >
          <span style={{ fontSize: 16 }}>{filtersOpen ? "⊟" : "⊞"}</span>
          {filtersOpen && "Filters"}
        </button>

        {filtersOpen && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div>
              <label
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#888",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  display: "block",
                  marginBottom: 8,
                }}
              >
                Year Range
              </label>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  value={localYearFrom}
                  onChange={(e) => setLocalYearFrom(e.target.value)}
                  onBlur={() => pushFilter({ year_from: localYearFrom })}
                  onKeyDown={(e) =>
                    e.key === "Enter" && pushFilter({ year_from: localYearFrom })
                  }
                  placeholder="From"
                  style={filterInputStyle}
                />
                <input
                  value={localYearTo}
                  onChange={(e) => setLocalYearTo(e.target.value)}
                  onBlur={() => pushFilter({ year_to: localYearTo })}
                  onKeyDown={(e) =>
                    e.key === "Enter" && pushFilter({ year_to: localYearTo })
                  }
                  placeholder="To"
                  style={filterInputStyle}
                />
              </div>
            </div>
            <div>
              <label
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#888",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  display: "block",
                  marginBottom: 8,
                }}
              >
                Min Citations
              </label>
              <input
                value={localMinCit}
                onChange={(e) => setLocalMinCit(e.target.value)}
                onBlur={() => pushFilter({ min_citations: localMinCit })}
                onKeyDown={(e) =>
                  e.key === "Enter" && pushFilter({ min_citations: localMinCit })
                }
                placeholder="e.g. 1000"
                style={{ ...filterInputStyle, width: "100%" }}
              />
            </div>
            <div>
              <label
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#888",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  display: "block",
                  marginBottom: 8,
                }}
              >
                Sort By
              </label>
              {["relevance", "newest"].map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    setLocalSort(s);
                    pushFilter({ sort: s });
                  }}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    padding: "5px 8px",
                    borderRadius: 6,
                    fontSize: 13,
                    marginBottom: 2,
                    background: localSort === s ? "#EEF3FF" : "none",
                    color: localSort === s ? "#2563EB" : "#555",
                    border: "none",
                    cursor: "pointer",
                    fontFamily: "var(--font-dm-sans), sans-serif",
                    fontWeight: localSort === s ? 500 : 400,
                  }}
                >
                  {s === "relevance" ? "Most Cited" : "Newest First"}
                </button>
              ))}
            </div>
          </div>
        )}
      </aside>

      {/* Main */}
      <div style={{ flex: 1 }}>
        {/* Header */}
        <div style={{ marginBottom: 24 }}>
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 12,
              marginBottom: 4,
            }}
          >
            <h1
              style={{
                fontFamily: "var(--font-dm-serif), Georgia, serif",
                fontSize: 22,
                fontWeight: 400,
                color: "#111",
                margin: 0,
              }}
            >
              {query ? `"${query}"` : "Latest Papers"}
            </h1>
            {!loading && total > 0 && (
              <span style={{ fontSize: 13, color: "#999" }}>
                {isDefaultView ? `${results.length} shown` : `${total.toLocaleString()} results`}
              </span>
            )}
          </div>
        </div>

        {loading &&
          Array.from({ length: 5 }).map((_, i) => <SkeletonPaperCard key={i} />)}

        {!loading && apiError && (
          <EmptyState
            message="Could not reach the backend"
            sub={`Make sure the API is running at ${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}`}
          />
        )}

        {!loading && !apiError && query.trim() && results.length === 0 && (
          <EmptyState
            message="No results found"
            sub="Try broadening your query or removing filters"
          />
        )}

        {!loading && !apiError && !query.trim() && results.length === 0 && (
          <EmptyState
            message="No papers in the database yet"
            sub="Start a crawl job to ingest papers from OpenAlex"
          />
        )}

        {!loading &&
          !apiError &&
          results.map((paper) => (
            <div
              key={paper.id}
              onMouseEnter={() => setHoveredId(paper.id)}
              onMouseLeave={() => setHoveredId(null)}
              style={{
                padding: "20px 0",
                borderBottom: "1px solid #EEECEA",
                position: "relative",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                  gap: 16,
                }}
              >
                <div style={{ flex: 1 }}>
                  <button
                    onClick={() => router.push(`/papers/${paper.id}`)}
                    style={{
                      fontFamily: "var(--font-dm-serif), Georgia, serif",
                      fontSize: 17,
                      fontWeight: 400,
                      color: "#111",
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      textAlign: "left",
                      padding: 0,
                      marginBottom: 6,
                      lineHeight: 1.35,
                    }}
                  >
                    {paper.title}
                  </button>

                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 12,
                      fontSize: 12,
                      color: "#888",
                      marginBottom: paper.abstract ? 8 : 0,
                    }}
                  >
                    {paper.year && (
                      <span style={{ fontWeight: 500, color: "#555" }}>
                        {paper.year}
                      </span>
                    )}
                    <span>·</span>
                    <span style={{ color: "#2563EB", fontWeight: 500 }}>
                      {fmt(paper.citation_count)} citations
                    </span>
                    {paper.source && (
                      <>
                        <span>·</span>
                        <span style={{ textTransform: "capitalize" }}>
                          {paper.source}
                        </span>
                      </>
                    )}
                  </div>

                  {paper.abstract && (
                    <p
                      style={{
                        fontSize: 13,
                        color: "#666",
                        lineHeight: 1.55,
                        margin: "6px 0 0",
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                      }}
                    >
                      {paper.abstract}
                    </p>
                  )}
                </div>

                {hoveredId === paper.id && (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 6,
                      flexShrink: 0,
                    }}
                  >
                    <button
                      onClick={() => router.push(`/papers/${paper.id}`)}
                      style={quickActionBtn}
                    >
                      Open
                    </button>
                    <button
                      onClick={() =>
                        router.push(`/graph?type=paper&id=${paper.id}`)
                      }
                      style={{
                        ...quickActionBtn,
                        background: "#EEF3FF",
                        color: "#2563EB",
                        borderColor: "#BFDBFE",
                      }}
                    >
                      Graph
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}

        {/* Pagination */}
        {!loading && !apiError && !isDefaultView && totalPages > 1 && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              paddingTop: 32,
            }}
          >
            <button
              onClick={() => pushFilter({ page: String(page - 1) })}
              disabled={page <= 1}
              style={{
                padding: "6px 14px",
                borderRadius: 8,
                fontSize: 13,
                border: "1px solid #DDD",
                background: "#FFF",
                cursor: page <= 1 ? "not-allowed" : "pointer",
                color: page <= 1 ? "#CCC" : "#444",
                fontFamily: "var(--font-dm-sans), sans-serif",
              }}
            >
              ← Prev
            </button>
            <span style={{ fontSize: 13, color: "#666" }}>
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => pushFilter({ page: String(page + 1) })}
              disabled={page >= totalPages}
              style={{
                padding: "6px 14px",
                borderRadius: 8,
                fontSize: 13,
                border: "1px solid #DDD",
                background: "#FFF",
                cursor: page >= totalPages ? "not-allowed" : "pointer",
                color: page >= totalPages ? "#CCC" : "#444",
                fontFamily: "var(--font-dm-sans), sans-serif",
              }}
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
