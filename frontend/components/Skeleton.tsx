import React from "react";

interface SkeletonProps {
  w?: string | number;
  h?: number;
  r?: number;
  style?: React.CSSProperties;
}

export function Skeleton({ w = "100%", h = 16, r = 6, style = {} }: SkeletonProps) {
  return (
    <div
      style={{
        width: w,
        height: h,
        borderRadius: r,
        background:
          "linear-gradient(90deg,#F0EEE8 25%,#E8E5DF 50%,#F0EEE8 75%)",
        backgroundSize: "200% 100%",
        animation: "shimmer 1.4s infinite",
        ...style,
      }}
    />
  );
}

export function SkeletonPaperCard() {
  return (
    <div style={{ padding: "20px 0", borderBottom: "1px solid #EEECEA" }}>
      <Skeleton h={18} w="70%" style={{ marginBottom: 10 }} />
      <Skeleton h={13} w="40%" style={{ marginBottom: 8 }} />
      <Skeleton h={13} w="55%" />
    </div>
  );
}

interface EmptyStateProps {
  message: string;
  sub?: string;
}

export function EmptyState({ message, sub }: EmptyStateProps) {
  return (
    <div style={{ textAlign: "center", padding: "80px 0", color: "#888" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>∅</div>
      <div
        style={{
          fontSize: 16,
          fontWeight: 500,
          color: "#555",
          marginBottom: 6,
        }}
      >
        {message}
      </div>
      {sub && <div style={{ fontSize: 13 }}>{sub}</div>}
    </div>
  );
}
