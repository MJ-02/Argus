"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

function TopBarInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const [inputVal, setInputVal] = useState(q);

  useEffect(() => {
    setInputVal(q);
  }, [q]);

  const currentPage = pathname === "/" ? "search" : pathname.startsWith("/graph") ? "graph" : "";

  const handleSubmit = () => {
    const trimmed = inputVal.trim();
    const params = new URLSearchParams(searchParams.toString());
    if (trimmed) {
      params.set("q", trimmed);
    } else {
      params.delete("q");
    }
    router.push(`/?${params.toString()}`);
  };

  return (
    <header
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 50,
        background: "rgba(253,252,250,0.92)",
        backdropFilter: "blur(12px)",
        borderBottom: "1px solid #E8E5E0",
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "0 32px",
        height: 56,
      }}
    >
      <button
        onClick={() => router.push("/")}
        style={{
          fontFamily: "var(--font-dm-serif), Georgia, serif",
          fontSize: 18,
          fontWeight: 400,
          color: "#111",
          letterSpacing: "-0.5px",
          background: "none",
          border: "none",
          cursor: "pointer",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        Argus<span style={{ color: "#2563EB" }}>.</span>
      </button>

      <div style={{ flex: 1, maxWidth: 520, position: "relative" }}>
        <span
          style={{
            position: "absolute",
            left: 12,
            top: "50%",
            transform: "translateY(-50%)",
            color: "#999",
            fontSize: 15,
            pointerEvents: "none",
          }}
        >
          ⌕
        </span>
        <input
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleSubmit();
          }}
          placeholder="Search papers, authors, topics…"
          style={{
            width: "100%",
            padding: "7px 12px 7px 36px",
            border: "1px solid #DDD",
            borderRadius: 10,
            fontSize: 14,
            background: "#F9F8F6",
            outline: "none",
            fontFamily: "var(--font-dm-sans), sans-serif",
            transition: "border-color 0.15s",
          }}
          onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
          onBlur={(e) => (e.target.style.borderColor = "#DDD")}
        />
      </div>

      <nav
        style={{
          display: "flex",
          gap: 4,
          marginLeft: "auto",
          flexShrink: 0,
        }}
      >
        {(
          [
            ["search", "Search", "/"],
            ["graph", "Graph", "/graph"],
          ] as const
        ).map(([page, label, href]) => (
          <button
            key={page}
            onClick={() => router.push(href)}
            style={{
              padding: "5px 12px",
              borderRadius: 8,
              fontSize: 13,
              fontWeight: 500,
              border:
                "1px solid " + (currentPage === page ? "#2563EB" : "transparent"),
              color: currentPage === page ? "#2563EB" : "#666",
              background: currentPage === page ? "#EEF3FF" : "none",
              cursor: "pointer",
              fontFamily: "var(--font-dm-sans), sans-serif",
            }}
          >
            {label}
          </button>
        ))}
      </nav>
    </header>
  );
}

export default function TopBar() {
  return (
    <Suspense
      fallback={
        <header
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 50,
            background: "rgba(253,252,250,0.92)",
            borderBottom: "1px solid #E8E5E0",
            height: 56,
          }}
        />
      }
    >
      <TopBarInner />
    </Suspense>
  );
}
