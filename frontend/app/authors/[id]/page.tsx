import type { Metadata } from "next";
import AuthorDetail from "@/components/AuthorDetail";

interface Props {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return {
    title: `Author ${id} — Argus`,
  };
}

export default async function AuthorPage({ params }: Props) {
  const { id } = await params;
  return <AuthorDetail authorId={id} />;
}
