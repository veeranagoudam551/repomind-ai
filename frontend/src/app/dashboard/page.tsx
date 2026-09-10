import Link from "next/link";
import { redirect } from "next/navigation";
import { ChevronLeft, ChevronRight, FolderGit, Trash2 } from "lucide-react";
import { AddRepositoryForm } from "@/components/add-repository-form";
import { removeRepository } from "@/app/actions/repositories";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  getCurrentUser,
  listRepositories,
  type Repository,
  type RepositoryStatus,
} from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

const STATUS_VARIANT: Record<RepositoryStatus, "secondary" | "default" | "destructive"> = {
  pending: "secondary",
  cloning: "secondary",
  processing: "secondary",
  completed: "default",
  failed: "destructive",
};

function parsePage(raw: string | undefined): number {
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
}

export default async function DashboardPage(props: PageProps<"/dashboard">) {
  const { page: pageParam } = await props.searchParams;
  const requestedPage = parsePage(typeof pageParam === "string" ? pageParam : undefined);

  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  let userEmail: string | null = null;
  let repositories: Repository[] = [];
  let total = 0;
  let totalPages = 0;
  let hasNext = false;
  let hasPrevious = false;
  let loadError: string | null = null;

  try {
    const [user, repoPage] = await Promise.all([
      getCurrentUser(token),
      listRepositories(token, { page: requestedPage }),
    ]);
    userEmail = user.email;
    repositories = repoPage.items;
    total = repoPage.total;
    totalPages = repoPage.total_pages;
    hasNext = repoPage.has_next;
    hasPrevious = repoPage.has_previous;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      await deleteSession();
      redirect("/login");
    }
    loadError = "Couldn't reach the RepoMind AI API. Is the backend running?";
  }

  // A page beyond the last one (e.g. a bookmarked link, or the last
  // repository on it got deleted) isn't an error - the backend returns an
  // empty items list for it - but "No repositories yet" would be
  // misleading here since the user does have repositories, just not on
  // this page.
  const isPastLastPage = repositories.length === 0 && total > 0;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10 sm:px-6">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Repositories</h1>
        <p className="text-sm text-muted-foreground">
          {userEmail ? `Signed in as ${userEmail}` : "Connect a GitHub repository to start asking questions about it."}
        </p>
      </div>

      <Card className="mb-8">
        <CardHeader>
          <CardTitle className="text-base">Add a repository</CardTitle>
          <CardDescription>Paste a public GitHub repo, e.g. octocat/Hello-World</CardDescription>
        </CardHeader>
        <CardContent>
          <AddRepositoryForm />
        </CardContent>
      </Card>

      {loadError && <p className="mb-4 text-sm text-destructive">{loadError}</p>}

      {!loadError && repositories.length === 0 && !isPastLastPage && (
        <p className="text-sm text-muted-foreground">No repositories yet — add one above.</p>
      )}

      {!loadError && isPastLastPage && (
        <p className="text-sm text-muted-foreground">
          Nothing on page {requestedPage}.{" "}
          <Link href="/dashboard" className="underline underline-offset-4">
            Back to page 1
          </Link>
          .
        </p>
      )}

      {repositories.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {repositories.map((repo) => (
            <Card key={repo.id}>
              <CardHeader>
                <div className="flex items-center justify-between gap-2">
                  <Link
                    href={`/dashboard/${repo.id}`}
                    className="flex min-w-0 items-center gap-2 hover:underline"
                  >
                    <FolderGit className="size-4 shrink-0 text-muted-foreground" />
                    <CardTitle className="truncate text-base">{repo.name}</CardTitle>
                  </Link>
                  <form action={removeRepository.bind(null, repo.id)}>
                    <Button type="submit" variant="ghost" size="icon-sm" aria-label="Delete repository">
                      <Trash2 />
                    </Button>
                  </form>
                </div>
                <CardDescription className="truncate">
                  {repo.description ?? "No description"}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex items-center justify-between gap-2">
                <Badge variant={STATUS_VARIANT[repo.status]}>{repo.status}</Badge>
                <span className="text-xs text-muted-foreground">{repo.file_count} files</span>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {!loadError && totalPages > 1 && (
        <div className="mt-6 flex items-center justify-between gap-2">
          {hasPrevious ? (
            <Button asChild variant="outline" size="sm">
              <Link href={requestedPage - 1 === 1 ? "/dashboard" : `/dashboard?page=${requestedPage - 1}`}>
                <ChevronLeft />
                Previous
              </Link>
            </Button>
          ) : (
            <Button variant="outline" size="sm" disabled>
              <ChevronLeft />
              Previous
            </Button>
          )}

          <span className="text-sm text-muted-foreground">
            Page {requestedPage} of {totalPages}
          </span>

          {hasNext ? (
            <Button asChild variant="outline" size="sm">
              <Link href={`/dashboard?page=${requestedPage + 1}`}>
                Next
                <ChevronRight />
              </Link>
            </Button>
          ) : (
            <Button variant="outline" size="sm" disabled>
              Next
              <ChevronRight />
            </Button>
          )}
        </div>
      )}
    </main>
  );
}
