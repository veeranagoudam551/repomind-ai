import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ApiError,
  describeApiError,
  explainRepositoryFile,
  getRepository,
  listRepositoryFiles,
} from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

export default async function ExplainFilePage(
  props: PageProps<"/dashboard/[id]/files/[fileId]/explain">
) {
  const { id, fileId } = await props.params;

  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  let repository;
  let files;
  try {
    [repository, files] = await Promise.all([
      getRepository(token, id),
      listRepositoryFiles(token, id),
    ]);
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

  const file = files.find((f) => f.id === fileId);
  if (!file) {
    notFound();
  }

  let explanation: string | undefined;
  let explainError: string | undefined;
  try {
    const result = await explainRepositoryFile(token, id, fileId);
    explanation = result.explanation;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    explainError = describeApiError(err);
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
          <CardTitle className="text-xl">Explain file</CardTitle>
          <CardDescription className="font-mono text-xs">{file.file_path}</CardDescription>
        </CardHeader>
        <CardContent>
          {explainError ? (
            <p className="text-sm text-destructive">{explainError}</p>
          ) : (
            <p className="whitespace-pre-wrap text-sm">{explanation}</p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
