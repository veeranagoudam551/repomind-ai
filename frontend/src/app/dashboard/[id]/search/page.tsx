import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, SearchIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  describeApiError,
  getRepository,
  searchRepository,
  type SearchResult,
} from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

export default async function RepositorySearchPage(props: PageProps<"/dashboard/[id]/search">) {
  const { id } = await props.params;
  const { q } = await props.searchParams;
  const query = typeof q === "string" ? q.trim() : "";

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

  let results: SearchResult[] = [];
  let searchError: string | undefined;
  if (query) {
    try {
      results = await searchRepository(token, id, query);
    } catch (err) {
      searchError = describeApiError(err);
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
          <CardTitle className="text-xl">Search code</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form className="flex gap-2">
            <Input
              name="q"
              defaultValue={query}
              placeholder="e.g. where is the JWT verified?"
              required
              className="flex-1"
            />
            <Button type="submit">
              <SearchIcon />
              Search
            </Button>
          </form>

          {searchError && <p className="text-sm text-destructive">{searchError}</p>}

          {!query && !searchError && (
            <p className="text-sm text-muted-foreground">
              Search {repository.name}&apos;s indexed code semantically — results are ranked by
              similarity, not just keyword match.
            </p>
          )}

          {query && !searchError && results.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No matches found for &quot;{query}&quot;.
            </p>
          )}

          {results.length > 0 && (
            <ul className="flex flex-col gap-3">
              {results.map((result) => (
                <SearchResultCard key={result.code_chunk_id} result={result} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </main>
  );
}

function SearchResultCard({ result }: { result: SearchResult }) {
  return (
    <li className="rounded-md border border-border">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-3 py-1.5">
        <span className="font-mono text-xs">
          {result.file_path}
          {result.start_line ? `:${result.start_line}-${result.end_line}` : ""}
        </span>
        <span className="text-xs text-muted-foreground">
          {(result.score * 100).toFixed(0)}% match
        </span>
      </div>
      <pre className="overflow-x-auto px-3 py-2 text-xs whitespace-pre-wrap">
        <code>{result.content}</code>
      </pre>
    </li>
  );
}
