import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, Bot, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, describeApiError, getRepository, runRepositoryAgent, type AgentStep } from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

const MAX_STEPS_OPTIONS = [2, 4, 6, 8, 10];
const DEFAULT_MAX_STEPS = 4;

export default async function RepositoryAgentPage(props: PageProps<"/dashboard/[id]/agent">) {
  const { id } = await props.params;
  const { goal: rawGoal, max_steps: rawMaxSteps } = await props.searchParams;
  const goal = typeof rawGoal === "string" ? rawGoal.trim() : "";
  const parsedMaxSteps = typeof rawMaxSteps === "string" ? Number(rawMaxSteps) : NaN;
  const maxSteps = MAX_STEPS_OPTIONS.includes(parsedMaxSteps) ? parsedMaxSteps : DEFAULT_MAX_STEPS;

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

  let answer: string | undefined;
  let steps: AgentStep[] = [];
  let agentError: string | undefined;
  if (goal) {
    try {
      const result = await runRepositoryAgent(token, id, goal, maxSteps);
      answer = result.answer;
      steps = result.steps;
    } catch (err) {
      agentError = describeApiError(err);
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
          <CardTitle className="text-xl">Agent</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form className="flex gap-2">
            <Input
              name="goal"
              defaultValue={goal}
              placeholder="e.g. figure out what this repo does and summarize its entry point"
              required
              className="flex-1"
            />
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Max steps
              <select
                name="max_steps"
                defaultValue={maxSteps}
                className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {MAX_STEPS_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <Button type="submit">
              <Bot />
              Run
            </Button>
          </form>

          {agentError && <p className="text-sm text-destructive">{agentError}</p>}

          {!goal && !agentError && (
            <p className="text-sm text-muted-foreground">
              Give the agent a goal for {repository.name} — it decides for itself which
              tools to call (search the code, explain a file) and how many steps to take,
              rather than following a fixed pipeline like Chat or Search.
            </p>
          )}

          {answer && (
            <div className="flex flex-col gap-3">
              <p className="rounded-md border border-border bg-muted p-3 text-sm whitespace-pre-wrap">
                {answer}
              </p>

              {steps.length > 0 && (
                <div className="flex flex-col gap-2">
                  <p className="text-xs font-medium text-muted-foreground">
                    Steps taken ({steps.length})
                  </p>
                  <ol className="flex flex-col gap-2">
                    {steps.map((step, index) => (
                      <StepCard key={index} step={step} index={index} />
                    ))}
                  </ol>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}

function StepCard({ step, index }: { step: AgentStep; index: number }) {
  return (
    <li className="rounded-md border border-border">
      <div className="flex items-center gap-2 border-b border-border bg-muted px-3 py-1.5">
        <Wrench className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-mono text-xs">
          {index + 1}. {step.tool}({JSON.stringify(step.arguments)})
        </span>
      </div>
      <pre className="overflow-x-auto px-3 py-2 text-xs whitespace-pre-wrap">
        <code>{step.summary}</code>
      </pre>
    </li>
  );
}
