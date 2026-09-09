"use client";

import { useActionState, useEffect, useRef } from "react";
import { Send } from "lucide-react";
import { sendMessageAction, type SendMessageFormState } from "@/app/actions/conversations";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function SendMessageForm({
  repositoryId,
  conversationId,
}: {
  repositoryId: string;
  conversationId: string;
}) {
  const boundAction = sendMessageAction.bind(null, repositoryId, conversationId);
  const [state, formAction, pending] = useActionState<SendMessageFormState, FormData>(
    boundAction,
    undefined
  );
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (!pending && !state?.error) {
      formRef.current?.reset();
    }
  }, [pending, state]);

  return (
    <form ref={formRef} action={formAction} className="flex flex-col gap-2">
      <div className="flex gap-2">
        <Input
          name="content"
          placeholder="Ask a question about this repository…"
          required
          disabled={pending}
          className="flex-1"
        />
        <Button type="submit" disabled={pending}>
          <Send />
          {pending ? "Thinking…" : "Send"}
        </Button>
      </div>
      {state?.error && <p className="text-sm text-destructive">{state.error}</p>}
    </form>
  );
}
