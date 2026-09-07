"use client";

import Link from "next/link";
import { useActionState } from "react";
import { Bot } from "lucide-react";
import { login, type AuthFormState } from "@/app/actions/auth";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginPage() {
  const [state, action, pending] = useActionState<AuthFormState, FormData>(login, undefined);

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16 sm:px-6">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <Bot className="size-8" />
          <CardTitle className="text-xl">Log in to RepoMind AI</CardTitle>
          <CardDescription>Use the account you registered with.</CardDescription>
        </CardHeader>
        <CardContent>
          <form action={action} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" placeholder="you@example.com" required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="password">Password</Label>
              <Input id="password" name="password" type="password" placeholder="••••••••" required />
            </div>
            {state?.error && <p className="text-sm text-destructive">{state.error}</p>}
            <Button className="w-full" type="submit" disabled={pending}>
              {pending ? "Logging in…" : "Log in"}
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <Link href="/register" className="font-medium text-foreground underline underline-offset-4">
              Create one
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
