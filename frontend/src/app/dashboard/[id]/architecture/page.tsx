import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { analyzeRepositoryArchitecture, ApiError, getRepository } from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

export default async function RepositoryArchitecturePage(
  props: PageProps<"/dashboard/[id]/architecture">
) {
  const { id } = await props.params;

  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  let repository;
  try {
    repository = await getRepository(token, id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      await deleteSession();
      redirect("/login");
    }
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  let analysis: string | undefined;
  let readmePath: string | null = null;
  let architectureError: string | undefined;
  try {
    const result = await analyzeRepositoryArchitecture(token, id);
    analysis = result.analysis;
    readmePath = result.readme_path;
  } catch (err) {
    architectureError =
      err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
  }

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-6">
      <Link
        href={`/dashboard/${id}`}
        className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        Back to {repository.name}
      </Link>

      <Card>
        <CardHeader>
          <CardTitle className="text-xl">Architecture</CardTitle>
          <CardDescription>
            Based on {repository.file_count} scanned file{repository.file_count === 1 ? "" : "s"}
            {readmePath ? ` and ${readmePath}` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {architectureError ? (
            <p className="text-sm text-destructive">{architectureError}</p>
          ) : (
            <p className="whitespace-pre-wrap text-sm">{analysis}</p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
