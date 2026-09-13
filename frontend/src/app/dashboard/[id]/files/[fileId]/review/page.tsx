import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ApiError,
  describeApiError,
  getRepository,
  listRepositoryFiles,
  reviewRepositoryFile,
} from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

export default async function ReviewFilePage(
  props: PageProps<"/dashboard/[id]/files/[fileId]/review">
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

  let review: string | undefined;
  let reviewError: string | undefined;
  try {
    const result = await reviewRepositoryFile(token, id, fileId);
    review = result.review;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    reviewError = describeApiError(err);
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
          <CardTitle className="text-xl">Review file</CardTitle>
          <CardDescription className="font-mono text-xs">{file.file_path}</CardDescription>
        </CardHeader>
        <CardContent>
          {reviewError ? (
            <p className="text-sm text-destructive">{reviewError}</p>
          ) : (
            <p className="whitespace-pre-wrap text-sm">{review}</p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
