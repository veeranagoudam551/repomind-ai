import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, ShieldAlert, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ApiError,
  getRepository,
  scanRepositorySecurity,
  type SecurityFinding,
  type SecuritySeverity,
} from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

const SEVERITY_VARIANT: Record<SecuritySeverity, "destructive" | "secondary" | "outline"> = {
  high: "destructive",
  medium: "secondary",
  low: "outline",
};

export default async function RepositorySecurityPage(
  props: PageProps<"/dashboard/[id]/security">
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

  let findings: SecurityFinding[] = [];
  let filesScanned: number | undefined;
  let scanError: string | undefined;
  try {
    const result = await scanRepositorySecurity(token, id);
    findings = result.findings;
    filesScanned = result.files_scanned;
  } catch (err) {
    scanError = err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
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
          <CardTitle className="text-xl">Security scan</CardTitle>
          <CardDescription>
            Basic pattern-based scan of {filesScanned ?? repository.file_count} file
            {(filesScanned ?? repository.file_count) === 1 ? "" : "s"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {scanError ? (
            <p className="text-sm text-destructive">{scanError}</p>
          ) : findings.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <ShieldCheck className="size-4 shrink-0" />
              No issues found by the basic pattern checks.
            </p>
          ) : (
            <ul className="flex flex-col gap-3">
              {findings.map((finding, index) => (
                <FindingCard key={`${finding.file_path}:${finding.line}:${index}`} finding={finding} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </main>
  );
}

function FindingCard({ finding }: { finding: SecurityFinding }) {
  return (
    <li className="rounded-md border border-border">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-3 py-1.5">
        <span className="font-mono text-xs">
          {finding.file_path}:{finding.line}
        </span>
        <Badge variant={SEVERITY_VARIANT[finding.severity]}>
          <ShieldAlert />
          {finding.severity}
        </Badge>
      </div>
      <div className="flex flex-col gap-2 px-3 py-2">
        <p className="text-sm">{finding.message}</p>
        <pre className="overflow-x-auto rounded bg-muted px-2 py-1 text-xs whitespace-pre-wrap">
          <code>{finding.snippet}</code>
        </pre>
      </div>
    </li>
  );
}
