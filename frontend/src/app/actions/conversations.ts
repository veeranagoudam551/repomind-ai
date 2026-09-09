"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { ApiError, createConversation, sendMessage } from "@/lib/api";
import { getSessionToken } from "@/lib/session";

export async function startConversation(repositoryId: string): Promise<void> {
  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  const conversation = await createConversation(token, repositoryId);
  revalidatePath(`/dashboard/${repositoryId}/chat`);
  redirect(`/dashboard/${repositoryId}/chat/${conversation.id}`);
}

export type SendMessageFormState = { error: string } | undefined;

export async function sendMessageAction(
  repositoryId: string,
  conversationId: string,
  _prevState: SendMessageFormState,
  formData: FormData
): Promise<SendMessageFormState> {
  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  const content = String(formData.get("content") ?? "").trim();
  if (!content) {
    return { error: "Type a question first." };
  }

  let error: string | undefined;
  try {
    await sendMessage(token, conversationId, content);
  } catch (err) {
    // The backend persists the user's turn before attempting the
    // embedding/LLM calls, so it's already saved even on failure here —
    // always revalidate below so that turn shows up rather than looking
    // like it silently vanished.
    error = err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
  }

  revalidatePath(`/dashboard/${repositoryId}/chat/${conversationId}`);
  return error ? { error } : undefined;
}
