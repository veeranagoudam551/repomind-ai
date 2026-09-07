import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function RepositoryNotFound() {
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col items-center justify-center gap-4 px-4 py-24 text-center sm:px-6">
      <h1 className="text-xl font-semibold">Repository not found</h1>
      <p className="text-sm text-muted-foreground">
        It may have been deleted, or it doesn&apos;t belong to your account.
      </p>
      <Button asChild size="sm">
        <Link href="/dashboard">Back to repositories</Link>
      </Button>
    </main>
  );
}
