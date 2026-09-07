import { redirect } from "next/navigation";
import { FolderGit, Trash2 } from "lucide-react";
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
import { ApiError, getCurrentUser, listRepositories, type RepositoryStatus } from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

const STATUS_VARIANT: Record<RepositoryStatus, "secondary" | "default" | "destructive"> = {
  pending: "secondary",
  cloning: "secondary",
  processing: "secondary",
  completed: "default",
  failed: "destructive",
};

export default async function DashboardPage() {
  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  let userEmail: string | null = null;
  let repositories: Awaited<ReturnType<typeof listRepositories>> = [];
  let loadError: string | null = null;

  try {
    const [user, repos] = await Promise.all([getCurrentUser(token), listRepositories(token)]);
    userEmail = user.email;
    repositories = repos;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      await deleteSession();
      redirect("/login");
    }
    loadError = "Couldn't reach the RepoMind AI API. Is the backend running?";
  }

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

      {!loadError && repositories.length === 0 && (
        <p className="text-sm text-muted-foreground">No repositories yet — add one above.</p>
      )}

      {repositories.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {repositories.map((repo) => (
            <Card key={repo.id}>
              <CardHeader>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <FolderGit className="size-4 shrink-0 text-muted-foreground" />
                    <CardTitle className="truncate text-base">{repo.name}</CardTitle>
                  </div>
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
    </main>
  );
}
