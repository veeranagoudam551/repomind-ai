import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, Bot, User as UserIcon } from "lucide-react";
import { SendMessageForm } from "@/components/send-message-form";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, getRepository, listMessages, type ChatMessage } from "@/lib/api";
import { deleteSession, getSessionToken } from "@/lib/session";

export default async function ConversationPage(
  props: PageProps<"/dashboard/[id]/chat/[conversationId]">
) {
  const { id, conversationId } = await props.params;

  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  let repository;
  let messages: ChatMessage[];

  try {
    [repository, messages] = await Promise.all([
      getRepository(token, id),
      listMessages(token, conversationId),
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
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-10 sm:px-6">
      <Link
        href={`/dashboard/${id}/chat`}
        className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        Back to conversations
      </Link>

      <Card className="flex flex-1 flex-col">
        <CardContent className="flex flex-1 flex-col gap-4 overflow-y-auto py-6">
          {messages.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Ask something about {repository.name} to get started.
            </p>
          ) : (
            messages.map((message) => <MessageBubble key={message.id} message={message} />)
          )}
        </CardContent>
        <div className="border-t border-border p-4">
          <SendMessageForm repositoryId={id} conversationId={conversationId} />
        </div>
      </Card>
    </main>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex items-start gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <Avatar size="sm">
        <AvatarFallback>
          {isUser ? <UserIcon className="size-4" /> : <Bot className="size-4" />}
        </AvatarFallback>
      </Avatar>
      <div className={`flex max-w-[80%] flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
        <div
          className={`rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
            isUser ? "bg-primary text-primary-foreground" : "bg-muted text-foreground"
          }`}
        >
          {message.content}
        </div>
        {message.sources.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {message.sources.map((source) => (
              <span
                key={source.code_chunk_id}
                className="rounded-full border border-border px-2 py-0.5 font-mono text-xs text-muted-foreground"
              >
                {source.file_path}
                {source.start_line ? `:${source.start_line}` : ""}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
