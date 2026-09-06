import Link from "next/link";
import { MessageSquareText, Search, Bug, FileCode2, ShieldCheck, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const FEATURES = [
  {
    icon: MessageSquareText,
    title: "Ask your codebase questions",
    description:
      "Get answers grounded in real source code via retrieval-augmented generation, not guesswork.",
  },
  {
    icon: Search,
    title: "Semantic code search",
    description: "Find relevant code by meaning, not just by keyword, across an entire repository.",
  },
  {
    icon: Bug,
    title: "AI-assisted debugging",
    description: "Investigate bugs with full repository context pulled in automatically.",
  },
  {
    icon: FileCode2,
    title: "Code review & explanation",
    description: "Understand unfamiliar code and get feedback on changes before they ship.",
  },
  {
    icon: ShieldCheck,
    title: "Architecture & security scanning",
    description: "Surface structural issues and basic defensive security concerns early.",
  },
  {
    icon: Workflow,
    title: "Specialized AI agents",
    description: "Hand off complex, multi-step engineering tasks to purpose-built agents.",
  },
] as const;

export default function Home() {
  return (
    <main className="flex flex-1 flex-col">
      <section className="mx-auto flex w-full max-w-4xl flex-col items-center gap-6 px-4 py-24 text-center sm:px-6">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Understand any codebase, faster
        </h1>
        <p className="max-w-2xl text-lg text-muted-foreground">
          Connect a GitHub repository and use AI to explore, understand, debug,
          and analyze it — grounded in the actual source code.
        </p>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link href="/dashboard">Go to dashboard</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href="/login">Log in</Link>
          </Button>
        </div>
      </section>

      <section className="mx-auto w-full max-w-6xl px-4 pb-24 sm:px-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, description }) => (
            <Card key={title}>
              <CardHeader>
                <Icon className="size-6 text-primary" />
                <CardTitle className="mt-2">{title}</CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-muted-foreground">
                {description}
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </main>
  );
}
