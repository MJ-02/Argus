import type { Metadata } from "next";
import PaperDetail from "@/components/PaperDetail";

interface Props {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return {
    title: `Paper ${id} — Argus`,
  };
}

export default async function PaperPage({ params }: Props) {
  const { id } = await params;
  return <PaperDetail paperId={id} />;
}
