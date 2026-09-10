import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import {
  ArrowLeft,
  Bug,
  ClipboardCheck,
  MessageSquare,
  Network,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  SearchIcon,
  Trash2,
} from "lucide-react";
import { deleteRepositoryAndRedirect, reindexRepositoryAction } from "@/app/actions/repositories";
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
  getRepository,
  listRepositoryFiles,
  type Repository,
  type RepositoryFile,
  type RepositoryStatus,
} from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";
import { formatBytes } from "@/lib/utils";

const STATUS_VARIANT: Record<RepositoryStatus, "secondary" | "default" | "destructive"> = {
  pending: "secondary",
  cloning: "secondary",
  processing: "secondary",
  completed: "default",
  failed: "destructive",
};

const IN_PROGRESS_STATUSES: RepositoryStatus[] = ["pending", "cloning", "processing"];

export default async function RepositoryDetailPage(props: PageProps<"/dashboard/[id]">) {
  const { id } = await props.params;

  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  let repository: Repository;
  let files: RepositoryFile[];

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

  const inProgress = IN_PROGRESS_STATUSES.includes(repository.status);

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10 sm:px-6">
      <Link
        href="/dashboard"
        className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        Back to repositories
      </Link>

      <Card className="mb-6">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="text-xl">{repository.name}</CardTitle>
              <CardDescription>
                <a
                  href={repository.github_url}
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-4"
                >
                  {repository.github_url}
                </a>
              </CardDescription>
            </div>
            <Badge variant={STATUS_VARIANT[repository.status]}>{repository.status}</Badge>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            {repository.description ?? "No description"}
          </p>

          {repository.status === "failed" && repository.error_message && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {repository.error_message}
            </p>
          )}

          <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <p className="text-muted-foreground">Default branch</p>
              <p className="font-medium">{repository.default_branch ?? "—"}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Files</p>
              <p className="font-medium">{repository.file_count}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Size</p>
              <p className="font-medium">{formatBytes(repository.total_size_bytes)}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Last updated</p>
              <p className="font-medium">{new Date(repository.updated_at).toLocaleString()}</p>
            </div>
          </div>

          <div className="flex gap-2">
            {repository.status === "completed" ? (
              <Button asChild size="sm">
                <Link href={`/dashboard/${repository.id}/chat`}>
                  <MessageSquare />
                  Chat
                </Link>
              </Button>
            ) : (
              <Button size="sm" disabled>
                <MessageSquare />
                Chat
              </Button>
            )}
            {repository.status === "completed" ? (
              <Button asChild variant="outline" size="sm">
                <Link href={`/dashboard/${repository.id}/search`}>
                  <SearchIcon />
                  Search
                </Link>
              </Button>
            ) : (
              <Button variant="outline" size="sm" disabled>
                <SearchIcon />
                Search
              </Button>
            )}
            {repository.status === "completed" ? (
              <Button asChild variant="outline" size="sm">
                <Link href={`/dashboard/${repository.id}/debug`}>
                  <Bug />
                  Debug
                </Link>
              </Button>
            ) : (
              <Button variant="outline" size="sm" disabled>
                <Bug />
                Debug
              </Button>
            )}
            {repository.file_count > 0 ? (
              <Button asChild variant="outline" size="sm">
                <Link href={`/dashboard/${repository.id}/architecture`}>
                  <Network />
                  Architecture
                </Link>
              </Button>
            ) : (
              <Button variant="outline" size="sm" disabled>
                <Network />
                Architecture
              </Button>
            )}
            {repository.file_count > 0 ? (
              <Button asChild variant="outline" size="sm">
                <Link href={`/dashboard/${repository.id}/security`}>
                  <ShieldAlert />
                  Security
                </Link>
              </Button>
            ) : (
              <Button variant="outline" size="sm" disabled>
                <ShieldAlert />
                Security
              </Button>
            )}
            <form action={reindexRepositoryAction.bind(null, repository.id)}>
              <Button type="submit" variant="outline" size="sm" disabled={inProgress}>
                <RefreshCw />
                {inProgress ? "Ingestion in progress…" : "Reindex"}
              </Button>
            </form>
            <form action={deleteRepositoryAndRedirect.bind(null, repository.id)}>
              <Button type="submit" variant="destructive" size="sm">
                <Trash2 />
                Delete
              </Button>
            </form>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Files ({files.length})</CardTitle>
          <CardDescription>
            {inProgress
              ? "Ingestion is still running — refresh to see more as they're scanned."
              : "Scanned during ingestion, ready for chunking and embedding."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {files.length === 0 ? (
            <p className="text-sm text-muted-foreground">No files scanned yet.</p>
          ) : (
            <div className="max-h-[32rem] overflow-y-auto rounded-md border border-border">
              <table className="w-full text-left text-sm">
                <thead className="sticky top-0 bg-muted text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">Path</th>
                    <th className="px-3 py-2 font-medium">Language</th>
                    <th className="px-3 py-2 text-right font-medium">Size</th>
                    <th className="px-3 py-2 font-medium">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {files.map((file) => (
                    <tr key={file.id}>
                      <td className="px-3 py-2 font-mono text-xs">{file.file_path}</td>
                      <td className="px-3 py-2 text-muted-foreground">{file.language ?? "—"}</td>
                      <td className="px-3 py-2 text-right text-muted-foreground">
                        {formatBytes(file.size_bytes)}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex justify-end gap-3">
                          <Link
                            href={`/dashboard/${repository.id}/files/${file.id}/explain`}
                            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                          >
                            <Sparkles className="size-3.5" />
                            Explain
                          </Link>
                          <Link
                            href={`/dashboard/${repository.id}/files/${file.id}/review`}
                            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                          >
                            <ClipboardCheck className="size-3.5" />
                            Review
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
