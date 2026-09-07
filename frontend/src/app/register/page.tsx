"use client";

import Link from "next/link";
import { useActionState } from "react";
import { Bot } from "lucide-react";
import { registerAndLogin, type AuthFormState } from "@/app/actions/auth";
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

export default function RegisterPage() {
  const [state, action, pending] = useActionState<AuthFormState, FormData>(
    registerAndLogin,
    undefined
  );

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16 sm:px-6">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <Bot className="size-8" />
          <CardTitle className="text-xl">Create your account</CardTitle>
          <CardDescription>Registers directly against the RepoMind AI API.</CardDescription>
        </CardHeader>
        <CardContent>
          <form action={action} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="full_name">Name</Label>
              <Input id="full_name" name="full_name" type="text" placeholder="Ada Lovelace" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" placeholder="you@example.com" required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                placeholder="••••••••"
                minLength={8}
                required
              />
            </div>
            {state?.error && <p className="text-sm text-destructive">{state.error}</p>}
            <Button className="w-full" type="submit" disabled={pending}>
              {pending ? "Creating account…" : "Create account"}
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="font-medium text-foreground underline underline-offset-4">
              Log in
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
