"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchAuthor, fetchAuthorPapers } from "@/lib/api";
import type { Author, Paper } from "@/types";
import { fmt } from "@/lib/utils";
import { EmptyState, Skeleton, SkeletonPaperCard } from "./Skeleton";

export default function AuthorDetail({ authorId }: { authorId: string }) {
  const router = useRouter();
  const [author, setAuthor] = useState<Author | null>(null);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [papersLoading, setPapersLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [sortBy, setSortBy] = useState<"citations" | "newest">("citations");
  const [page, setPage] = useState(1);
  const LIMIT = 20;

  useEffect(() => {
    setLoading(true);
    setNotFound(false);
    fetchAuthor(authorId).then((a) => {
      if (!a) setNotFound(true);
      setAuthor(a);
      setLoading(false);
    });
  }, [authorId]);

  useEffect(() => {
    if (!author) return;
    setPapersLoading(true);
    fetchAuthorPapers(author.id, LIMIT, (page - 1) * LIMIT).then((res) => {
      if (res) {
        setPapers(res.items);
        setTotal(res.total);
      }
      setPapersLoading(false);
    });
  }, [author, page]);

  const sortedPapers = [...papers].sort((a, b) =>
    sortBy === "citations"
      ? b.citation_count - a.citation_count
      : (b.year ?? 0) - (a.year ?? 0)
  );

  const totalPages = Math.ceil(total / LIMIT);

  if (loading) {
    return (
      <div style={{ maxWidth: 1100, margin: "88px auto 48px", padding: "0 24px" }}>
        <Skeleton h={32} w="40%" style={{ marginBottom: 12 }} />
        <Skeleton h={16} w="30%" style={{ marginBottom: 32 }} />
        <Skeleton h={16} w="50%" />
      </div>
    );
  }

  if (notFound || !author) {
    return (
      <div style={{ maxWidth: 1100, margin: "88px auto 48px", padding: "0 24px" }}>
        <EmptyState
          message="Author not found"
          sub={`No author with ID "${authorId}" exists in the database`}
        />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "88px 24px 64px" }}>
      <button
        onClick={() => router.back()}
        style={{
          fontSize: 12,
          color: "#888",
          background: "none",
          border: "none",
          cursor: "pointer",
          marginBottom: 20,
          fontFamily: "var(--font-dm-sans), sans-serif",
        }}
      >
        ← Back
      </button>

      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          marginBottom: 36,
          gap: 24,
        }}
      >
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 16,
              marginBottom: 8,
            }}
          >
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: "50%",
                background: "linear-gradient(135deg,#EEF3FF,#DBEAFE)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 22,
                color: "#2563EB",
                fontFamily: "var(--font-dm-serif), Georgia, serif",
                flexShrink: 0,
              }}
            >
              {author.name.charAt(0)}
            </div>
            <div>
              <h1
                style={{
                  fontFamily: "var(--font-dm-serif), Georgia, serif",
                  fontSize: 26,
                  fontWeight: 400,
                  color: "#111",
                  margin: 0,
                }}
              >
                {author.name}
              </h1>
            </div>
          </div>
          <div
            style={{
              display: "flex",
              gap: 16,
              fontSize: 13,
              color: "#666",
              paddingLeft: 72,
            }}
          >
            <span>
              <b style={{ color: "#111" }}>{author.works_count}</b> works
            </span>
            <span>·</span>
            <span>
              <b style={{ color: "#2563EB" }}>{fmt(author.citation_count)}</b>{" "}
              citations
            </span>
            {author.orcid && (
              <>
                <span>·</span>
                <a
                  href={`https://orcid.org/${author.orcid}`}
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "#888", fontSize: 12 }}
                >
                  ORCID ↗
                </a>
              </>
            )}
          </div>
        </div>
        <button
          onClick={() => router.push(`/graph?type=author&id=${author.id}`)}
          style={{
            padding: "9px 18px",
            borderRadius: 10,
            fontSize: 13,
            fontWeight: 500,
            background: "#111",
            color: "#FFF",
            border: "none",
            cursor: "pointer",
            fontFamily: "var(--font-dm-sans), sans-serif",
            flexShrink: 0,
          }}
        >
          Network Graph ↗
        </button>
      </div>

      {/* Publications */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: "#888",
            letterSpacing: "0.08em",
            textTransform: "uppercase",
          }}
        >
          Publications{total > 0 && ` (${total.toLocaleString()})`}
        </div>
        <div style={{ display: "flex", gap: 2 }}>
          {(
            [
              ["citations", "Most Cited"],
              ["newest", "Newest First"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setSortBy(key)}
              style={{
                padding: "4px 10px",
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 500,
                background: sortBy === key ? "#111" : "none",
                color: sortBy === key ? "#FFF" : "#666",
                border: "1px solid " + (sortBy === key ? "#111" : "#DDD"),
                cursor: "pointer",
                fontFamily: "var(--font-dm-sans), sans-serif",
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {papersLoading &&
        Array.from({ length: 5 }).map((_, i) => <SkeletonPaperCard key={i} />)}

      {!papersLoading && sortedPapers.length === 0 && (
        <EmptyState
          message="No publications found"
          sub="This author has no papers recorded in the database"
        />
      )}

      {!papersLoading &&
        sortedPapers.map((p) => (
          <div
            key={p.id}
            style={{ padding: "16px 0", borderBottom: "1px solid #EEECEA" }}
          >
            <button
              onClick={() => router.push(`/papers/${p.id}`)}
              style={{
                fontFamily: "var(--font-dm-serif), Georgia, serif",
                fontSize: 16,
                color: "#111",
                background: "none",
                border: "none",
                cursor: "pointer",
                textAlign: "left",
                padding: 0,
                display: "block",
                marginBottom: 5,
                lineHeight: 1.35,
              }}
            >
              {p.title}
            </button>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                fontSize: 12,
                color: "#888",
              }}
            >
              {p.year && (
                <span style={{ fontWeight: 500, color: "#333" }}>{p.year}</span>
              )}
              <span>·</span>
              <span style={{ color: "#2563EB" }}>
                {fmt(p.citation_count)} citations
              </span>
              {p.source && (
                <>
                  <span>·</span>
                  <span style={{ textTransform: "capitalize" }}>{p.source}</span>
                </>
              )}
            </div>
          </div>
        ))}

      {/* Pagination */}
      {!papersLoading && totalPages > 1 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            paddingTop: 24,
          }}
        >
          <button
            onClick={() => setPage((p) => p - 1)}
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
            onClick={() => setPage((p) => p + 1)}
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
  );
}
