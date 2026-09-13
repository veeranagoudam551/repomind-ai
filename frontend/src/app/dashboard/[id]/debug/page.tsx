import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, Bug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, debugRepository, describeApiError, getRepository, type SearchResult } from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

export default async function RepositoryDebugPage(props: PageProps<"/dashboard/[id]/debug">) {
  const { id } = await props.params;
  const { description: rawDescription } = await props.searchParams;
  const description = typeof rawDescription === "string" ? rawDescription.trim() : "";

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

  let diagnosis: string | undefined;
  let sources: SearchResult[] = [];
  let debugError: string | undefined;
  if (description) {
    try {
      const result = await debugRepository(token, id, description);
      diagnosis = result.diagnosis;
      sources = result.sources;
    } catch (err) {
      debugError = describeApiError(err);
    }
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
          <CardTitle className="text-xl">Debug</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form className="flex gap-2">
            <Input
              name="description"
              defaultValue={description}
              placeholder="Describe the bug or paste an error message…"
              required
              className="flex-1"
            />
            <Button type="submit">
              <Bug />
              Diagnose
            </Button>
          </form>

          {debugError && <p className="text-sm text-destructive">{debugError}</p>}

          {!description && !debugError && (
            <p className="text-sm text-muted-foreground">
              Describe a bug or paste an error from {repository.name} — the diagnosis is
              grounded in the repository&apos;s actual indexed code, not guesswork.
            </p>
          )}

          {diagnosis && (
            <div className="flex flex-col gap-3">
              <p className="rounded-md border border-border bg-muted p-3 text-sm whitespace-pre-wrap">
                {diagnosis}
              </p>

              {sources.length > 0 && (
                <ul className="flex flex-col gap-3">
                  {sources.map((source) => (
                    <SourceCard key={source.code_chunk_id} source={source} />
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}

function SourceCard({ source }: { source: SearchResult }) {
  return (
    <li className="rounded-md border border-border">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-3 py-1.5">
        <span className="font-mono text-xs">
          {source.file_path}
          {source.start_line ? `:${source.start_line}-${source.end_line}` : ""}
        </span>
        <span className="text-xs text-muted-foreground">{(source.score * 100).toFixed(0)}% match</span>
      </div>
      <pre className="overflow-x-auto px-3 py-2 text-xs whitespace-pre-wrap">
        <code>{source.content}</code>
      </pre>
    </li>
  );
}
