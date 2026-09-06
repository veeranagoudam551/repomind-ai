import { FolderGit, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const PLACEHOLDER_REPOS = [
  { name: "octocat/hello-world", status: "not connected" },
  { name: "your-org/your-service", status: "not connected" },
] as const;

export default function DashboardPage() {
  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10 sm:px-6">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Repositories</h1>
          <p className="text-sm text-muted-foreground">
            Connect a GitHub repository to start asking questions about it.
          </p>
        </div>
        <Button disabled>
          <Plus />
          Add repository
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {PLACEHOLDER_REPOS.map((repo) => (
          <Card key={repo.name}>
            <CardHeader>
              <div className="flex items-center gap-2">
                <FolderGit className="size-4 text-muted-foreground" />
                <CardTitle className="text-base">{repo.name}</CardTitle>
              </div>
              <CardDescription>Sample placeholder — repository ingestion isn&apos;t built yet.</CardDescription>
            </CardHeader>
            <CardContent>
              <Badge variant="secondary">{repo.status}</Badge>
            </CardContent>
          </Card>
        ))}
      </div>
    </main>
  );
}
