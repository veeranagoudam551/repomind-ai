import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, MessageSquarePlus } from "lucide-react";
import { startConversation } from "@/app/actions/conversations";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError, getRepository, listConversations, type Conversation } from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

export default async function RepositoryChatListPage(props: PageProps<"/dashboard/[id]/chat">) {
  const { id } = await props.params;

  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  let repository;
  let conversations: Conversation[];

  try {
    [repository, conversations] = await Promise.all([
      getRepository(token, id),
      listConversations(token, id),
    ]);
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

  return (
    <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-10 sm:px-6">
      <Link
        href={`/dashboard/${id}`}
        className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        Back to {repository.name}
      </Link>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <div>
            <CardTitle className="text-xl">Chat</CardTitle>
            <CardDescription>
              Ask questions grounded in {repository.name}&apos;s indexed code.
            </CardDescription>
          </div>
          <form action={startConversation.bind(null, id)}>
            <Button type="submit" size="sm">
              <MessageSquarePlus />
              New chat
            </Button>
          </form>
        </CardHeader>
        <CardContent>
          {conversations.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No conversations yet — start one to ask something about this repository.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {conversations.map((conversation) => (
                <li key={conversation.id}>
                  <Link
                    href={`/dashboard/${id}/chat/${conversation.id}`}
                    className="flex items-center justify-between gap-3 py-3 text-sm hover:text-foreground"
                  >
                    <span className="font-medium">{conversation.title ?? "Untitled chat"}</span>
                    <span className="text-xs text-muted-foreground">
                      {new Date(conversation.updated_at).toLocaleString()}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
