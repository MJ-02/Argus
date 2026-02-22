"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchCitationGraph, fetchPaper } from "@/lib/api";
import type { APICitationGraph, Paper } from "@/types";
import { fmt } from "@/lib/utils";
import { EmptyState, Skeleton } from "./Skeleton";

function MiniPaperRow({
  paper,
  onOpen,
}: {
  paper: { id: string; title: string | null; year?: number | null; citation_count?: number | null };
  onOpen: (id: string) => void;
}) {
  return (
    <div style={{ padding: "14px 0", borderBottom: "1px solid #EEECEA" }}>
      <button
        onClick={() => onOpen(paper.id)}
        style={{
          fontFamily: "var(--font-dm-serif), Georgia, serif",
          fontSize: 15,
          color: "#111",
          background: "none",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          padding: 0,
          display: "block",
          marginBottom: 4,
          lineHeight: 1.4,
        }}
      >
        {paper.title ?? paper.id}
      </button>
      <div style={{ fontSize: 12, color: "#888" }}>
        {paper.year && <span>{paper.year}</span>}
        {paper.citation_count != null && (
          <>
            {paper.year && <span style={{ margin: "0 6px" }}>·</span>}
            <span style={{ color: "#2563EB" }}>
              {fmt(paper.citation_count)} citations
            </span>
          </>
        )}
      </div>
    </div>
  );
}

export default function PaperDetail({ paperId }: { paperId: string }) {
  const router = useRouter();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [citGraph, setCitGraph] = useState<APICitationGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [citLoading, setCitLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [citDepth, setCitDepth] = useState(1);

  useEffect(() => {
    setLoading(true);
    setNotFound(false);
    fetchPaper(paperId).then((p) => {
      if (!p) setNotFound(true);
      setPaper(p);
      setLoading(false);
    });
  }, [paperId]);

  useEffect(() => {
    if (!paper) return;
    setCitLoading(true);
    setCitGraph(null);
    fetchCitationGraph(paper.id, citDepth).then((g) => {
      setCitGraph(g);
      setCitLoading(false);
    });
  }, [paper, citDepth]);

  if (loading) {
    return (
      <div style={{ maxWidth: 1100, margin: "88px auto 48px", padding: "0 24px" }}>
        <Skeleton h={32} w="60%" style={{ marginBottom: 16 }} />
        <Skeleton h={16} w="40%" style={{ marginBottom: 8 }} />
        <Skeleton h={16} w="30%" style={{ marginBottom: 32 }} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: 32 }}>
          <Skeleton h={200} />
          <Skeleton h={200} />
        </div>
      </div>
    );
  }

  if (notFound || !paper) {
    return (
      <div style={{ maxWidth: 1100, margin: "88px auto 48px", padding: "0 24px" }}>
        <EmptyState
          message="Paper not found"
          sub={`No paper with ID "${paperId}" exists in the database`}
        />
      </div>
    );
  }

  const citNodes = citGraph?.nodes.filter((n) => n.id !== paper.id) ?? [];
  const citEdges = citGraph?.edges ?? [];

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "88px 24px 64px" }}>
      {/* Header */}
      <div style={{ marginBottom: 32 }}>
        <button
          onClick={() => router.back()}
          style={{
            fontSize: 12,
            color: "#888",
            background: "none",
            border: "none",
            cursor: "pointer",
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontFamily: "var(--font-dm-sans), sans-serif",
          }}
        >
          ← Back
        </button>

        <h1
          style={{
            fontFamily: "var(--font-dm-serif), Georgia, serif",
            fontSize: 28,
            fontWeight: 400,
            color: "#111",
            lineHeight: 1.3,
            margin: "0 0 16px",
          }}
        >
          {paper.title}
        </h1>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            fontSize: 13,
            color: "#666",
            flexWrap: "wrap",
          }}
        >
          {paper.year && (
            <span style={{ fontWeight: 500, color: "#333" }}>{paper.year}</span>
          )}
          <span>·</span>
          <span style={{ color: "#2563EB", fontWeight: 600 }}>
            {fmt(paper.citation_count)} citations
          </span>
          {paper.source && (
            <>
              <span>·</span>
              <span style={{ textTransform: "capitalize", color: "#888" }}>
                {paper.source}
              </span>
            </>
          )}
          {paper.doi && (
            <>
              <span>·</span>
              <a
                href={`https://doi.org/${paper.doi}`}
                target="_blank"
                rel="noreferrer"
                style={{ color: "#888", fontSize: 12 }}
              >
                DOI ↗
              </a>
            </>
          )}
        </div>
      </div>

      {/* Body */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 260px",
          gap: 32,
          alignItems: "start",
        }}
      >
        {/* Left column */}
        <div>
          {paper.abstract && (
            <div
              style={{
                border: "1px solid #E8E5E0",
                borderRadius: 12,
                padding: "20px 24px",
                marginBottom: 28,
                background: "#FEFDFB",
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#888",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  marginBottom: 12,
                }}
              >
                Abstract
              </div>
              <p
                style={{
                  fontSize: 14,
                  color: "#333",
                  lineHeight: 1.7,
                  margin: 0,
                  fontFamily: "var(--font-dm-sans), sans-serif",
                }}
              >
                {paper.abstract}
              </p>
            </div>
          )}

          {/* Citation graph section */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
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
              Citation Graph
            </div>
            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "#AAA" }}>Depth</span>
              {[1, 2, 3].map((d) => (
                <button
                  key={d}
                  onClick={() => setCitDepth(d)}
                  style={{
                    padding: "2px 9px",
                    borderRadius: 6,
                    fontSize: 12,
                    border: "1px solid " + (citDepth === d ? "#2563EB" : "#DDD"),
                    background: citDepth === d ? "#EEF3FF" : "#FFF",
                    color: citDepth === d ? "#2563EB" : "#555",
                    cursor: "pointer",
                    fontFamily: "var(--font-dm-sans), sans-serif",
                  }}
                >
                  {d}
                </button>
              ))}
              {citEdges.length > 0 && (
                <span style={{ fontSize: 11, color: "#AAA", marginLeft: 4 }}>
                  {citNodes.length} nodes · {citEdges.length} edges
                </span>
              )}
            </div>
          </div>

          {citLoading && (
            <>
              <Skeleton h={52} style={{ marginBottom: 8 }} />
              <Skeleton h={52} style={{ marginBottom: 8 }} />
              <Skeleton h={52} />
            </>
          )}

          {!citLoading && citGraph === null && (
            <EmptyState
              message="No citation data"
              sub="Run a crawl to populate the citation graph"
            />
          )}

          {!citLoading && citGraph !== null && citNodes.length === 0 && (
            <EmptyState
              message="No outgoing citations recorded"
              sub="This paper has no citation relationships in the graph yet"
            />
          )}

          {!citLoading &&
            citNodes.map((n) => (
              <MiniPaperRow
                key={n.id}
                paper={n}
                onOpen={(id) => router.push(`/papers/${id}`)}
              />
            ))}
        </div>

        {/* Right rail */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div
            style={{
              border: "1px solid #E8E5E0",
              borderRadius: 12,
              padding: "16px 20px",
              background: "#FEFDFB",
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: "#888",
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                marginBottom: 14,
              }}
            >
              At a Glance
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 12,
                marginBottom: 16,
              }}
            >
              {(
                [
                  ["Citations", fmt(paper.citation_count), "#2563EB"],
                  ["Year", paper.year ?? "—", "#059669"],
                  ["In graph", citNodes.length || "—", "#D97706"],
                  ["Depth", citDepth, "#7C3AED"],
                ] as const
              ).map(([label, val, color]) => (
                <div
                  key={label}
                  style={{
                    background: "#F7F6F4",
                    borderRadius: 8,
                    padding: "10px 12px",
                  }}
                >
                  <div
                    style={{
                      fontSize: 18,
                      fontWeight: 700,
                      color,
                      fontFamily: "var(--font-dm-sans), sans-serif",
                    }}
                  >
                    {val}
                  </div>
                  <div style={{ fontSize: 11, color: "#888", marginTop: 2 }}>
                    {label}
                  </div>
                </div>
              ))}
            </div>

            <button
              onClick={() => router.push(`/graph?type=paper&id=${paper.id}`)}
              style={{
                width: "100%",
                padding: "9px",
                borderRadius: 9,
                fontSize: 13,
                fontWeight: 500,
                background: "#111",
                color: "#FFF",
                border: "none",
                cursor: "pointer",
                fontFamily: "var(--font-dm-sans), sans-serif",
              }}
            >
              Open in Graph Explorer ↗
            </button>
          </div>

          {paper.id && (
            <div
              style={{
                border: "1px solid #E8E5E0",
                borderRadius: 12,
                padding: "16px 20px",
                background: "#FEFDFB",
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#888",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  marginBottom: 10,
                }}
              >
                Identifiers
              </div>
              <div style={{ fontSize: 12, color: "#555" }}>
                <div style={{ marginBottom: 6 }}>
                  <span style={{ color: "#AAA" }}>ID </span>
                  <span
                    style={{
                      fontFamily: "monospace",
                      fontSize: 11,
                      background: "#F3F4F6",
                      padding: "1px 5px",
                      borderRadius: 4,
                    }}
                  >
                    {paper.id}
                  </span>
                </div>
                {paper.doi && (
                  <div>
                    <span style={{ color: "#AAA" }}>DOI </span>
                    <a
                      href={`https://doi.org/${paper.doi}`}
                      target="_blank"
                      rel="noreferrer"
                      style={{
                        color: "#2563EB",
                        fontSize: 11,
                        fontFamily: "monospace",
                      }}
                    >
                      {paper.doi} ↗
                    </a>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
