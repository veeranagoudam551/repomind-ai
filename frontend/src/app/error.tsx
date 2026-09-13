"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

// Root error boundary (Next.js App Router convention - must be a Client
// Component). Catches whatever a Server Component throws that isn't
// already handled inline: every dashboard page already redirects on a 401
// and calls notFound() on a 404 (see e.g. dashboard/[id]/page.tsx), so
// this only ever fires for something genuinely unexpected - a 5xx, a
// network failure, a real bug - not the ordinary "not logged in"/"not
// found" cases those pages already render specific UI for.
//
// `error.digest` is a Next.js-internal id for its own server-side logs,
// not this app's own X-Request-ID (see lib/api.ts's ApiError.requestId) -
// and in production Next.js already strips the original error's message
// and stack before a Client Component boundary like this one ever sees
// it, so there is nothing reliable of the backend's own to show here
// anyway. Deliberately not reading `error` at all: this boundary shows
// only its own generic, static message, never a digest, stack trace, or
// any other error detail.
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="mx-auto flex min-h-[60vh] w-full max-w-4xl flex-1 flex-col items-center justify-center gap-4 px-4 py-24 text-center sm:px-6">
      <AlertTriangle className="size-8 text-muted-foreground" aria-hidden="true" />
      <h1 className="text-xl font-semibold">Something went wrong</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        An unexpected error occurred. You can try again, or head back to your
        repositories.
      </p>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={() => reset()}>
          Try again
        </Button>
        <Button asChild size="sm">
          <Link href="/dashboard">Back to repositories</Link>
        </Button>
      </div>
    </main>
  );
}
