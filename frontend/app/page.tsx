import { Suspense } from "react";
import SearchPage from "@/components/SearchPage";
import { SkeletonPaperCard } from "@/components/Skeleton";

export default function Home() {
  return (
    <Suspense
      fallback={
        <div
          style={{
            maxWidth: 1100,
            margin: "0 auto",
            padding: "88px 24px 48px",
          }}
        >
          {Array.from({ length: 5 }).map((_, i) => (
            <SkeletonPaperCard key={i} />
          ))}
        </div>
      }
    >
      <SearchPage />
    </Suspense>
  );
}
