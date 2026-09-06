import Link from "next/link";
import { Bot } from "lucide-react";
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
  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16 sm:px-6">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <Bot className="size-8" />
          <CardTitle className="text-xl">Log in to RepoMind AI</CardTitle>
          <CardDescription>
            Authentication isn&apos;t wired up yet — this is a placeholder for
            the sign-in flow.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" placeholder="you@example.com" disabled />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" placeholder="••••••••" disabled />
          </div>
          <Button className="w-full" disabled>
            Log in (coming soon)
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Just exploring?{" "}
            <Link href="/dashboard" className="font-medium text-foreground underline underline-offset-4">
              Preview the dashboard
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
