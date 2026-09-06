export function SiteFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-2 px-4 py-6 text-sm text-muted-foreground sm:flex-row sm:px-6">
        <p>&copy; {new Date().getFullYear()} RepoMind AI. All rights reserved.</p>
        <p>Grounded in your code, not guesswork.</p>
      </div>
    </footer>
  );
}
